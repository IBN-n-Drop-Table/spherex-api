#!/usr/bin/env python3
"""Offline Golden Path generator for the SPHEREx citizen-science API.

For each target this script:
  1. Queries IRSA SIA for SPHEREx Level 2 Spectral Image MEFs (wide survey,
     falling back to the deep fields when coverage is sparse).
  2. Groups exposures into survey visits by `t_min` and picks two epochs
     ~6 months apart.
  3. Downloads a small server-side cutout of every exposure (IRSA cutout
     service: same MEF layout, ~100x fewer bytes than a 66 MB full frame),
     subtracts ZODI from IMAGE, evaluates the spectral WCS at the target,
     measures aperture photometry, and reprojects IMAGE/VARIANCE/FLAGS onto
     a common north-up grid centred on the target at epoch 1.
     The grid covers exactly --fov arcmin; the --png-size PNGs are an exact
     integer upscale of it, described by the `patch.wcs` block in the JSON.
  4. Renders broadband RGB, H2O / CO2 filter and difference PNGs.
  5. Detects anomalies in (epoch 2 - epoch 1) with OpenCV, merges split
     contours closer than --dedup-radius-px, and measures a 102-band
     spectrum at every anomaly.
  6. Bins photometry into the 102 LVF channels and builds a light curve.
  7. Validates against SkyPatchResponse and writes app/data/<target>.json.

The same code path serves live (uncached) API jobs through `build_payload`.

Run `python scripts/generate_golden_path.py --help` for options.
"""

from __future__ import annotations

import argparse
import http.client
import io
import json
import logging
import math
import os
import sys
import time
import urllib.error
import warnings
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np
from astropy import log as astropy_log
from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.nddata import Cutout2D
from astropy.nddata.utils import NoOverlapError
from astropy.stats import sigma_clipped_stats
from astropy.table import Table, unique, vstack
from astropy.time import Time
from astropy.utils.data import conf
from astropy.visualization import AsinhStretch, ZScaleInterval
from astropy.wcs import WCS, FITSFixedWarning
from astroquery.ipac.irsa import Irsa
from matplotlib import colormaps
from matplotlib.colors import LinearSegmentedColormap
from PIL import Image
from pyvo.dal.exceptions import DALServiceError
from reproject import reproject_interp

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.core.patch_geometry import (  # noqa: E402
    grid_to_png,
    grid_to_png_float,
    patch_wcs,
    stack_grid_size,
    suppress_duplicates,
)
from app.core.targets import CATALOG, Target  # noqa: E402
from app.schemas.sky_patch import SkyPatchResponse  # noqa: E402

log = logging.getLogger("golden_path")

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
WIDE_COLLECTIONS = ["spherex_dr1", "spherex_qr3"]  # priority order for dedup
DEEP_COLLECTIONS = ["spherex_dr1_deep", "spherex_qr3_deep"]

RETRYABLE_ERRORS = (
    TimeoutError,
    urllib.error.HTTPError,
    urllib.error.URLError,
    http.client.IncompleteRead,
    ConnectionError,
    DALServiceError,  # IRSA SIA answers 502 "Proxy Error" when busy
)

PIXEL_SCALE_ARCSEC = 6.15  # SPHEREx native plate scale

# LVF band edges per detector (um). 6 detectors x 17 channels = 102.
LVF_BANDS: dict[int, tuple[float, float]] = {
    1: (0.75, 1.09),
    2: (1.10, 1.62),
    3: (1.63, 2.41),
    4: (2.42, 3.82),
    5: (3.83, 4.41),
    6: (4.42, 5.00),
}
CHANNELS_PER_DETECTOR = 17

CHEMICAL_WINDOWS_UM: dict[str, list[tuple[float, float]]] = {
    "H2O": [(1.4, 1.9), (2.6, 2.8)],
    "CO2": [(4.2, 4.4)],
}
CHEMICAL_CMAPS = {
    "H2O": LinearSegmentedColormap.from_list("h2o", ["#000000", "#1f5fff", "#d6ecff"]),
    "CO2": LinearSegmentedColormap.from_list("co2", ["#000000", "#d7301f", "#ffe0c8"]),
}
RGB_DETECTORS = {"R": (5, 6), "G": (3, 4), "B": (1, 2)}

# A detection is "clean" when none of these are set at its centroid.
ARTIFACT_FLAGS = {"HOT", "NONFUNC", "OUTLIER", "SNOWBALL", "BLOOM", "GHOST", "STREAK"}
# Pixels carrying these are excluded from stacks and photometry.
STACK_MASK_FLAGS = ARTIFACT_FLAGS | {"TRANSIENT", "OVERFLOW", "SUR_ERROR", "MISSING_DATA"}
# Used only if an exposure's FLAGS header carries no MSKNnnnn keywords.
DEFAULT_FLAG_BITS = {0: "TRANSIENT", 19: "OUTLIER", 21: "SOURCE", 26: "BLOOM", 27: "SNOWBALL"}


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #
class StorageUploader(ABC):
    """Persists a PNG and returns the public URL the frontend will load."""

    @abstractmethod
    def upload_png(self, filename: str, data: bytes) -> str: ...


class LocalStorage(StorageUploader):
    """Writes under app/static/<url_path>/, which the API serves at /static/<url_path>/."""

    def __init__(self, base_url: str, url_path: str = "golden_path") -> None:
        self.url_path = url_path.strip("/")
        self.directory = REPO_ROOT / "app" / "static" / self.url_path
        self.directory.mkdir(parents=True, exist_ok=True)
        self.base_url = base_url.rstrip("/")

    def upload_png(self, filename: str, data: bytes) -> str:
        (self.directory / filename).write_bytes(data)
        return f"{self.base_url}/static/{self.url_path}/{filename}"


class SupabaseStorage(StorageUploader):
    """Uploads to a *public* Supabase Storage bucket."""

    def __init__(self, prefix: str = "golden_path") -> None:
        try:
            from supabase import create_client
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise SystemExit("pip install supabase  (needed for --storage supabase)") from exc
        missing = [k for k in ("SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_BUCKET") if not os.getenv(k)]
        if missing:
            raise SystemExit(f"Missing env vars for Supabase storage: {', '.join(missing)}")
        client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
        self.bucket = client.storage.from_(os.environ["SUPABASE_BUCKET"])
        self.prefix = prefix

    def upload_png(self, filename: str, data: bytes) -> str:
        path = f"{self.prefix}/{filename}"
        self.bucket.upload(
            path=path,
            file=data,
            file_options={"content-type": "image/png", "upsert": "true", "cache-control": "300"},
        )
        return self.bucket.get_public_url(path).rstrip("?")


def make_uploader(kind: str, public_base_url: str) -> StorageUploader:
    return SupabaseStorage() if kind == "supabase" else LocalStorage(public_base_url)


# --------------------------------------------------------------------------- #
# Astrometry & IRSA access
# --------------------------------------------------------------------------- #
def position_at(target: Target, when: Time) -> SkyCoord:
    """Linear proper-motion propagation (accurate to <0.1" over decades here)."""
    dt = when.jyear - target.ref_epoch_yr
    dec = target.ref_dec_deg + target.pm_dec_mas_yr * dt / 3.6e6
    mean_dec = math.radians((dec + target.ref_dec_deg) / 2)
    ra = target.ref_ra_deg + target.pm_ra_cosdec_mas_yr * dt / 3.6e6 / math.cos(mean_dec)
    return SkyCoord(ra % 360.0, dec, unit="deg")


def with_retry(fn, what: str, attempts: int = 3, base_delay: float = 5.0):
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except RETRYABLE_ERRORS as exc:
            if attempt == attempts:
                raise
            delay = base_delay * 2 ** (attempt - 1)
            log.warning("%s failed (%s: %s); retry %d/%d in %.0fs",
                        what, type(exc).__name__, exc, attempt, attempts - 1, delay)
            time.sleep(delay)


def query_sia(coord: SkyCoord, collections: list[str]) -> Table:
    rows = with_retry(
        lambda: Irsa.query_sia(pos=(coord, 1 * u.arcsec), collection=collections),
        what=f"SIA query {collections}",
    )
    rows = Table(rows)
    if len(rows) and "dataproduct_subtype" in rows.colnames:
        rows = rows[rows["dataproduct_subtype"] == "science"]
    return rows


def dedupe(rows: Table, priority: list[str]) -> Table:
    """Same exposure can appear in several releases; keep the preferred one."""
    if not len(rows):
        return rows
    rank = {c: i for i, c in enumerate(priority)}
    rows["_rank"] = [rank.get(str(c), 99) for c in rows["obs_collection"]]
    rows.sort(["_rank", "t_min"])
    rows = unique(rows, keys=["obs_id", "energy_bandpassname"], keep="first")
    rows.remove_column("_rank")
    rows.sort("t_min")
    return rows


def group_epochs(rows: Table, gap_days: float) -> list[Table]:
    """Split time-sorted rows into survey visits wherever t_min jumps > gap_days."""
    if not len(rows):
        return []
    rows = rows.copy()
    rows.sort("t_min")
    mjd = np.asarray(rows["t_min"], dtype=float)
    breaks = np.where(np.diff(mjd) > gap_days)[0] + 1
    return [rows[idx] for idx in np.split(np.arange(len(rows)), breaks)]


def pick_epochs(groups: list[Table], strategy: str, min_rows: int) -> tuple[Table, Table] | None:
    usable = [g for g in groups if len(g) >= min_rows]
    if len(usable) < 2:
        return None
    if strategy == "first-last":
        return usable[0], usable[-1]
    medians = [float(np.median(g["t_min"])) for g in usable]
    best, best_score = None, -1
    for i in range(len(usable)):
        for j in range(i + 1, len(usable)):
            if 120 <= medians[j] - medians[i] <= 240:  # ~6 months
                score = min(len(usable[i]), len(usable[j]))
                if score > best_score:
                    best, best_score = (usable[i], usable[j]), score
    return best or (usable[0], usable[-1])


def find_epoch_rows(target: Target, args) -> tuple[Table, Table]:
    conf.remote_timeout = 120
    coord = position_at(target, Time("2026-01-01T00:00:00", scale="utc"))
    rows = dedupe(query_sia(coord, WIDE_COLLECTIONS), WIDE_COLLECTIONS)
    log.info("SIA wide survey: %d exposures", len(rows))
    pair = pick_epochs(group_epochs(rows, args.epoch_gap_days), args.epoch_strategy, args.min_exposures)
    if pair is None:
        log.info("Wide coverage sparse; adding deep-field collections")
        deep = query_sia(coord, DEEP_COLLECTIONS)
        if len(deep):
            rows = dedupe(vstack([rows, deep]) if len(rows) else deep,
                          WIDE_COLLECTIONS + DEEP_COLLECTIONS)
        pair = pick_epochs(group_epochs(rows, args.epoch_gap_days), args.epoch_strategy, args.min_exposures)
    if pair is None:
        raise RuntimeError(
            f"Need two visits with >= {args.min_exposures} exposures each; got "
            f"{[len(g) for g in group_epochs(rows, args.epoch_gap_days)]}"
        )
    return pair


def subsample(rows: Table, limit: int | None) -> Table:
    """Evenly thin an epoch while keeping every detector represented."""
    if not limit or len(rows) <= limit:
        return rows
    rows = rows.copy()
    rows.sort(["energy_bandpassname", "t_min"])
    return rows[np.unique(np.linspace(0, len(rows) - 1, limit).astype(int))]


def open_fits_with_retry(url: str, use_cache: bool) -> fits.HDUList:
    return with_retry(lambda: fits.open(url, memmap=False, cache=use_cache), what=f"GET {url[-60:]}")


# --------------------------------------------------------------------------- #
# Per-exposure processing
# --------------------------------------------------------------------------- #
@dataclass
class Exposure:
    obs_id: str
    detector: int
    mjd: float
    wavelength_um: float
    bandwidth_um: float
    image: np.ndarray  # zodi-subtracted, on reference grid, artifacts = NaN
    variance: np.ndarray  # on reference grid
    flags: np.ndarray  # raw bitmask on reference grid (float, NaN = no data)
    wave_map: np.ndarray  # LVF wavelength (um) at each reference-grid pixel
    bw_map: np.ndarray  # LVF bandwidth (um) at each reference-grid pixel
    flag_names: dict[int, str]
    ap_flux: float | None  # target photometry, measured on the native cutout
    ap_var: float | None


@dataclass(frozen=True)
class Sample:
    """One exposure's photometry at one sky position."""

    detector: int
    wavelength_um: float
    bandwidth_um: float
    flux: float
    var: float


def decode_flag_names(header: fits.Header) -> dict[int, str]:
    names = {}
    for key in header:
        if key.startswith("MSKN") and key[4:].isdigit():
            names[int(key[4:])] = str(header[key]).strip().upper()
    return names or dict(DEFAULT_FLAG_BITS)


def flag_names_at(flag_int: int, names: dict[int, str]) -> list[str]:
    return [name for bit, name in sorted(names.items()) if bool(flag_int & (1 << int(bit)))]


def aperture_photometry(
    clean: np.ndarray, var: np.ndarray, x: float, y: float, r_ap: float, r_in: float, r_out: float
) -> tuple[float | None, float | None]:
    """Mean surface brightness (MJy/sr) in a circular aperture, minus a
    sigma-clipped annulus background. Keeps the schema's MJy/sr unit."""
    yy, xx = np.indices(clean.shape)
    r = np.hypot(xx - x, yy - y)
    ap = (r <= r_ap) & np.isfinite(clean) & np.isfinite(var)
    n_expected = max(int((r <= r_ap).sum()), 1)
    if ap.sum() < max(1, n_expected // 2):
        return None, None
    ann = (r >= r_in) & (r <= r_out) & np.isfinite(clean)
    bkg = sigma_clipped_stats(clean[ann], sigma=3.0)[1] if ann.sum() >= 8 else 0.0
    n = ap.sum()
    flux = float(np.mean(clean[ap]) - bkg)
    variance = float(np.sum(var[ap]) / n**2)
    return flux, variance


def process_row(row, target: Target, ref_wcs: WCS, ref_shape: tuple[int, int], args) -> Exposure | None:
    when = Time(float(row["t_min"]), format="mjd", scale="utc")
    coord = position_at(target, when)
    support_arcmin = args.fov * math.sqrt(2) + 1.5  # covers a rotated fov + annulus
    url = (f"{row['access_url']}?center={coord.ra.deg:.6f},{coord.dec.deg:.6f}"
           f"&size={support_arcmin / 60:.4f}")

    with open_fits_with_retry(url, use_cache=not args.no_cache) as hdulist:
        header = hdulist["IMAGE"].header
        clean = hdulist["IMAGE"].data.astype(np.float64) - hdulist["ZODI"].data.astype(np.float64)
        variance = hdulist["VARIANCE"].data.astype(np.float64)
        flags_raw = hdulist["FLAGS"].data.astype(np.int64)
        flag_names = decode_flag_names(hdulist["FLAGS"].header)

        spatial_wcs = WCS(header)
        x, y = (float(v) for v in spatial_wcs.world_to_pixel(coord))
        ny, nx = clean.shape
        if not (0 <= x < nx and 0 <= y < ny):
            return None

        spectral_wcs = WCS(header=header, fobj=hdulist, key="W")
        spectral_wcs.sip = None
        wl, bw = spectral_wcs.pixel_to_world(x, y)
        wavelength_um = float(wl.to_value(u.um))
        bandwidth_um = float(bw.to_value(u.um))

        # The LVF wavelength changes across the detector, so anomaly spectra
        # need the wavelength at their own position, not at the target's.
        gy, gx = np.indices(ref_shape)
        grid_sky = deepcopy(ref_wcs).pixel_to_world(gx.ravel(), gy.ravel())
        raw_x, raw_y = spatial_wcs.world_to_pixel(grid_sky)
        wl_grid, bw_grid = spectral_wcs.pixel_to_world(raw_x, raw_y)
        wave_map = np.asarray(wl_grid.to_value(u.um), dtype=np.float32).reshape(ref_shape)
        bw_map = np.asarray(bw_grid.to_value(u.um), dtype=np.float32).reshape(ref_shape)

    detector = header.get("DETECTOR")
    if detector is None:
        detector = int(str(row["energy_bandpassname"]).rsplit("D", 1)[-1])
    if not math.isfinite(wavelength_um):
        return None

    mask_bits = sum(1 << b for b, n in flag_names.items() if n in STACK_MASK_FLAGS)
    bad = (flags_raw & mask_bits) != 0
    clean[bad] = np.nan
    variance[bad] = np.nan

    ap_flux, ap_var = aperture_photometry(
        clean, variance, x, y, args.aperture_px, args.annulus_px[0], args.annulus_px[1]
    )

    size = args.fov * math.sqrt(2) * u.arcmin
    reprojected = []
    try:
        for data, order in ((clean, "bilinear"), (variance, "bilinear"),
                            (flags_raw.astype(np.float64), "nearest-neighbor")):
            cut = Cutout2D(data, coord, size, wcs=spatial_wcs, mode="partial", fill_value=np.nan)
            out = reproject_interp((cut.data, cut.wcs), deepcopy(ref_wcs), shape_out=ref_shape,
                                   order=order, return_footprint=False)
            reprojected.append(out)
    except NoOverlapError:
        return None

    return Exposure(
        obs_id=str(row["obs_id"]),
        detector=int(detector),
        mjd=float(row["t_min"]),
        wavelength_um=wavelength_um,
        bandwidth_um=bandwidth_um,
        image=reprojected[0],
        variance=reprojected[1],
        flags=reprojected[2],
        wave_map=wave_map,
        bw_map=bw_map,
        flag_names=flag_names,
        ap_flux=ap_flux,
        ap_var=ap_var,
    )


def process_rows(rows: Table, target: Target, ref_wcs: WCS, ref_shape, args, label: str) -> list[Exposure]:
    out: list[Exposure] = []
    failures = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(process_row, row, target, ref_wcs, ref_shape, args): i
                   for i, row in enumerate(rows)}
        for n, fut in enumerate(as_completed(futures), 1):
            try:
                exp = fut.result()
                if exp is not None:
                    out.append(exp)
            except Exception as exc:  # one bad exposure must not kill the run
                failures += 1
                log.warning("[%s] exposure %d skipped: %s: %s", label, futures[fut], type(exc).__name__, exc)
            if n % 20 == 0 or n == len(futures):
                log.info("[%s] %d/%d exposures processed", label, n, len(futures))
    log.info("[%s] %d usable, %d failed, %d off-target", label, len(out), failures,
             len(rows) - len(out) - failures)
    return sorted(out, key=lambda e: e.mjd)


# --------------------------------------------------------------------------- #
# Stacking
# --------------------------------------------------------------------------- #
def build_reference_wcs(center: SkyCoord, fov_arcmin: float, png_size: int) -> tuple[WCS, tuple[int, int]]:
    """North-up, square-pixel TAN grid covering exactly fov_arcmin, at about
    native SPHEREx sampling (5' -> 50 px of 6.0"). It has the same centre and
    footprint as the PNG `patch.wcs`, which is this grid scaled by png_size / n."""
    n = stack_grid_size(fov_arcmin, png_size, PIXEL_SCALE_ARCSEC)
    scale_deg = fov_arcmin / 60.0 / n
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = [center.ra.deg, center.dec.deg]
    w.wcs.crpix = [(n + 1) / 2, (n + 1) / 2]
    w.wcs.cdelt = [-scale_deg, scale_deg]
    w.wcs.cunit = ["deg", "deg"]
    return w, (n, n)


def median_stack(images: list[np.ndarray], variances: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    cube, vcube = np.stack(images), np.stack(variances)
    n = np.isfinite(cube).sum(axis=0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        med = np.nanmedian(cube, axis=0)
        var = (np.pi / 2) * np.nanmean(vcube, axis=0) / np.maximum(n, 1)  # variance of a median
    med[n == 0] = np.nan
    var[n == 0] = np.nan
    # Remove the residual sky pedestal (ZODI model error differs per visit),
    # otherwise it dominates the epoch difference and tints the RGB.
    finite = np.isfinite(med)
    if finite.sum() >= 10:
        med -= sigma_clipped_stats(med[finite], sigma=3.0)[1]
    return med, var


def mean_of(stacks: list[tuple[np.ndarray, np.ndarray]]) -> tuple[np.ndarray, np.ndarray]:
    imgs = np.stack([s[0] for s in stacks])
    vars_ = np.stack([s[1] for s in stacks])
    n = np.isfinite(imgs).sum(axis=0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        img = np.nanmean(imgs, axis=0)
        var = np.nansum(vars_, axis=0) / np.maximum(n, 1) ** 2
    img[n == 0] = np.nan
    var[n == 0] = np.nan
    return img, var


@dataclass
class EpochProduct:
    epoch_id: str
    when: datetime
    exposures: list[Exposure]
    detector_stacks: dict[int, tuple[np.ndarray, np.ndarray]] = field(default_factory=dict)
    flag_fraction: dict[int, np.ndarray] = field(default_factory=dict)
    flag_names: dict[int, str] = field(default_factory=dict)

    @classmethod
    def build(cls, epoch_id: str, exposures: list[Exposure]) -> EpochProduct:
        if not exposures:
            raise RuntimeError(f"Epoch {epoch_id} has no usable exposures")
        mjd = float(np.median([e.mjd for e in exposures]))
        when = Time(mjd, format="mjd", scale="utc").to_datetime(timezone=UTC).replace(microsecond=0)
        prod = cls(epoch_id=epoch_id, when=when, exposures=exposures)
        for det in sorted({e.detector for e in exposures}):
            members = [e for e in exposures if e.detector == det]
            prod.detector_stacks[det] = median_stack([e.image for e in members],
                                                     [e.variance for e in members])
        # Per-bit fraction of covering exposures with that flag set.
        for e in exposures:
            prod.flag_names.update(e.flag_names)
        shape = exposures[0].flags.shape
        coverage = np.zeros(shape)
        counts = {bit: np.zeros(shape) for bit in prod.flag_names}
        for e in exposures:
            valid = np.isfinite(e.flags)
            fi = np.where(valid, e.flags, 0).astype(np.int64)
            coverage += valid
            for bit in counts:
                counts[bit] += ((fi >> bit) & 1) * valid
        prod.flag_fraction = {b: c / np.maximum(coverage, 1) for b, c in counts.items()}
        return prod

    def stack_of(self, detectors) -> tuple[np.ndarray, np.ndarray] | None:
        stacks = [self.detector_stacks[d] for d in detectors if d in self.detector_stacks]
        return mean_of(stacks) if stacks else None

    def chemical_stack(self, molecule: str) -> np.ndarray | None:
        members = [e for e in self.exposures
                   if any(lo <= e.wavelength_um <= hi for lo, hi in CHEMICAL_WINDOWS_UM[molecule])]
        return median_stack([e.image for e in members], [e.variance for e in members])[0] if members else None

    def flags_at(self, row: int, col: int, majority: float) -> set[str]:
        return {self.flag_names[b] for b, frac in self.flag_fraction.items() if frac[row, col] >= majority}


# --------------------------------------------------------------------------- #
# Rendering (all arrays are FITS-oriented: row 0 = south; PNGs are north-up)
# --------------------------------------------------------------------------- #
def display_limits(*images: np.ndarray | None) -> tuple[float, float]:
    """Shared limits so epochs are directly comparable: ZScale floor,
    99.7th-percentile ceiling (keeps bright stars from clipping early)."""
    vals = np.concatenate([im[np.isfinite(im)].ravel() for im in images if im is not None])
    if vals.size == 0:
        return 0.0, 1.0
    lo, hi = ZScaleInterval().get_limits(vals)
    hi = max(hi, float(np.percentile(vals, 99.7)))
    return float(lo), float(hi) if hi > lo else float(lo) + 1.0


def asinh_stretch(img: np.ndarray, limits: tuple[float, float], a: float = 0.1) -> np.ndarray:
    lo, hi = limits
    scaled = np.clip((img - lo) / (hi - lo), 0, 1)
    out = AsinhStretch(a=a)(np.nan_to_num(scaled, nan=0.0))
    return np.clip(out, 0, 1)


def to_png(rgb: np.ndarray, size: int) -> bytes:
    """rgb: (H, W, 3) floats in [0, 1], FITS orientation."""
    arr = (np.flipud(np.clip(rgb, 0, 1)) * 255).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr, mode="RGB").resize((size, size), Image.Resampling.NEAREST).save(buf, "PNG", optimize=True)
    return buf.getvalue()


def render_rgb(epoch: EpochProduct, limits: dict[str, tuple[float, float]], size: int) -> bytes:
    channels = {c: epoch.stack_of(dets) for c, dets in RGB_DETECTORS.items()}
    fallback = epoch.stack_of(LVF_BANDS)[0]
    planes = [asinh_stretch(channels[c][0] if channels[c] else fallback, limits[c]) for c in "RGB"]
    return to_png(np.dstack(planes), size)


def render_cmap(img: np.ndarray, limits: tuple[float, float], cmap, size: int) -> bytes:
    rgba = cmap(asinh_stretch(img, limits))
    return to_png(rgba[..., :3], size)


def render_difference(diff: np.ndarray, size: int) -> bytes:
    finite = diff[np.isfinite(diff)]
    lim = float(np.percentile(np.abs(finite), 99.5)) if finite.size else 1.0
    lim = lim or 1.0
    rgba = colormaps["RdBu_r"]((np.clip(diff / lim, -1, 1) + 1) / 2)
    rgba[~np.isfinite(diff)] = (0, 0, 0, 1)
    return to_png(rgba[..., :3], size)


# --------------------------------------------------------------------------- #
# Science products
# --------------------------------------------------------------------------- #
def sig(x: float, digits: int = 6) -> float:
    return float(f"{x:.{digits}g}")


def detect_anomalies(
    diff: np.ndarray, diff_var: np.ndarray, e1: EpochProduct, e2: EpochProduct,
    ref_wcs: WCS, args,
) -> list[dict]:
    """Anomalies in diff = e2 - e1 (epoch_a = e2, epoch_b = e1)."""
    snr = diff / np.sqrt(diff_var)
    finite = np.isfinite(snr)
    if finite.sum() < 10:
        return []
    # Re-normalise so the threshold is in *empirical* sigma (L2 variances
    # don't capture residual systematics between visits).
    _, med, std = sigma_clipped_stats(snr[finite], sigma=3.0)
    z = np.where(finite, (snr - med) / (std or 1.0), 0.0).astype(np.float32)

    candidates = []
    for sign in (1, -1):  # brightening and fading
        _, mask = cv2.threshold(sign * z, args.snr_threshold, 255, cv2.THRESH_BINARY)
        contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            region = np.zeros(z.shape, np.uint8)
            cv2.drawContours(region, [contour], -1, 1, thickness=cv2.FILLED)
            region = region.astype(bool) & finite
            if region.sum() < args.min_anomaly_px:
                continue
            w = np.abs(diff[region])
            rows, cols = np.nonzero(region)
            y = float(np.average(rows, weights=w))
            x = float(np.average(cols, weights=w))
            snr_ap = abs(float(np.sum(diff[region]))) / math.sqrt(float(np.sum(diff_var[region])))
            candidates.append((snr_ap, x, y, sign))

    # Split contours of one source (e.g. the two lobes of a slightly
    # mis-subtracted star) land a few px apart: keep the strongest only.
    n_grid = z.shape[0]
    png_xy = [grid_to_png_float(x, y, n_grid, args.png_size) for _, x, y, _ in candidates]
    kept = suppress_duplicates([(c[0], *xy) for c, xy in zip(candidates, png_xy)], args.dedup_radius_px)
    if len(kept) < len(candidates):
        log.info("Merged %d duplicate detection(s) within %.0f px", len(candidates) - len(kept),
                 args.dedup_radius_px)

    anomalies = []
    for i, idx in enumerate(kept[: args.max_anomalies], 1):
        snr_ap, x, y, sign = candidates[idx]
        r, c = int(round(y)), int(round(x))
        flags = sorted(e1.flags_at(r, c, args.flag_majority) | e2.flags_at(r, c, args.flag_majority))
        world = ref_wcs.pixel_to_world(x, y)
        px, py = grid_to_png(x, y, n_grid, args.png_size)
        # Measure where the source is present: epoch 2 if it brightened, epoch 1 if it faded.
        source_epoch = e2 if sign > 0 else e1
        spectrum = binned_spectrum(grid_samples(source_epoch.exposures, x, y, args))
        anomalies.append({
            "anomaly_id": f"anom_{i:02d}",
            "ra_deg": round(float(world.ra.deg), 6),
            "dec_deg": round(float(world.dec.deg), 6),
            "pixel_x": px,
            "pixel_y": py,
            "snr_confidence": round(snr_ap, 2),
            "contamination_flags": flags,
            "is_clean_detection": not (set(flags) & ARTIFACT_FLAGS),
            "spectrum_epoch_id": source_epoch.epoch_id,
            "spectrum_102_bands": sorted(spectrum.values(), key=lambda b: b["wavelength_um"]),
        })
    return anomalies


def target_samples(exposures: list[Exposure]) -> list[Sample]:
    return [Sample(e.detector, e.wavelength_um, e.bandwidth_um, e.ap_flux, e.ap_var)
            for e in exposures if e.ap_flux is not None and e.ap_var is not None]


def grid_samples(exposures: list[Exposure], x: float, y: float, args) -> list[Sample]:
    """Aperture photometry at a reference-grid position in every exposure.

    Uses the reprojected images (bilinear, ~native scale). The variance is
    slightly underestimated because neighbouring reprojected pixels are
    correlated, which is acceptable for display spectra."""
    r, c = int(round(y)), int(round(x))
    out = []
    for e in exposures:
        wl, bw = float(e.wave_map[r, c]), float(e.bw_map[r, c])
        if not (math.isfinite(wl) and math.isfinite(bw) and wl > 0 and bw > 0):
            continue
        flux, var = aperture_photometry(e.image, e.variance, x, y,
                                        args.aperture_px, args.annulus_px[0], args.annulus_px[1])
        if flux is not None and var is not None:
            out.append(Sample(e.detector, wl, bw, flux, var))
    return out


def binned_spectrum(samples: list[Sample]) -> dict[tuple[int, int], dict]:
    """Inverse-variance mean of aperture photometry in 6 x 17 LVF channels."""
    buckets: dict[tuple[int, int], list[Sample]] = {}
    for s in samples:
        if not (s.var > 0 and math.isfinite(s.flux)) or s.detector not in LVF_BANDS:
            continue
        lo, hi = LVF_BANDS[s.detector]
        edges = np.geomspace(lo, hi, CHANNELS_PER_DETECTOR + 1)
        ch = int(np.clip(np.searchsorted(edges, s.wavelength_um) - 1, 0, CHANNELS_PER_DETECTOR - 1))
        buckets.setdefault((s.detector, ch), []).append(s)

    spectrum = {}
    for key, members in buckets.items():
        w = np.array([1 / m.var for m in members])
        spectrum[key] = {
            "wavelength_um": sig(float(np.average([m.wavelength_um for m in members], weights=w)), 5),
            "bandwidth_um": sig(float(np.mean([m.bandwidth_um for m in members])), 4),
            "flux_mjy_sr": sig(float(np.average([m.flux for m in members], weights=w))),
            "variance": sig(float(1 / w.sum())),
            "detector": key[0],
        }
    return spectrum


def light_curve_point(epoch: EpochProduct, spec: dict, common: set) -> dict:
    keys = common or set(spec)
    fluxes = [spec[k]["flux_mjy_sr"] for k in keys]
    variances = [spec[k]["variance"] for k in keys]
    return {
        "epoch_id": epoch.epoch_id,
        "date": epoch.when.date().isoformat(),
        "flux_mjy_sr": sig(float(np.mean(fluxes))) if fluxes else 0.0,
        "variance": sig(float(np.sum(variances) / len(variances) ** 2)) if variances else 0.0,
    }


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def build_payload(target: Target, uploader: StorageUploader, args, cached: bool = True) -> dict:
    """Run the whole pipeline for one position; return a SkyPatchResponse-shaped dict."""
    log.info("=== %s (%s) ===", target.name, target.key)
    rows1, rows2 = find_epoch_rows(target, args)
    rows1, rows2 = subsample(rows1, args.max_exposures_per_epoch), subsample(rows2, args.max_exposures_per_epoch)

    t1 = Time(float(np.median(rows1["t_min"])), format="mjd", scale="utc")
    t2 = Time(float(np.median(rows2["t_min"])), format="mjd", scale="utc")
    months = round((t2 - t1).to_value(u.day) / 30.44)
    id1, id2 = f"{t1.datetime.year}_M0", f"{t1.datetime.year}_M{months}"
    log.info("Epochs: %s (%s, %d exp) vs %s (%s, %d exp)",
             id1, t1.iso[:10], len(rows1), id2, t2.iso[:10], len(rows2))

    center = position_at(target, t1)
    ref_wcs, ref_shape = build_reference_wcs(center, args.fov, args.png_size)
    log.info("Reference grid %dx%d px centred on %.6f, %.6f (epoch-1 position)",
             *ref_shape, center.ra.deg, center.dec.deg)

    e1 = EpochProduct.build(id1, process_rows(rows1, target, ref_wcs, ref_shape, args, id1))
    e2 = EpochProduct.build(id2, process_rows(rows2, target, ref_wcs, ref_shape, args, id2))

    # Broadband = mean of per-detector median stacks, over detectors both epochs share,
    # so the difference isn't biased by which LVF channels each visit sampled.
    common_dets = sorted(set(e1.detector_stacks) & set(e2.detector_stacks))
    if not common_dets:
        raise RuntimeError("Epochs share no detectors; cannot difference")
    bb1, var1 = e1.stack_of(common_dets)
    bb2, var2 = e2.stack_of(common_dets)
    diff, diff_var = bb2 - bb1, var1 + var2

    size = args.png_size
    slug = target.slug
    epochs_json = []
    rgb_limits = {c: display_limits(*(s[0] for e in (e1, e2) if (s := e.stack_of(d))))
                  for c, d in RGB_DETECTORS.items()}
    chem = {m: (e1.chemical_stack(m), e2.chemical_stack(m)) for m in CHEMICAL_WINDOWS_UM}
    chem_limits = {m: display_limits(*imgs) for m, imgs in chem.items() if any(i is not None for i in imgs)}

    for n, epoch in enumerate((e1, e2), 1):
        filters = {}
        for molecule, imgs in chem.items():
            img = imgs[n - 1]
            if img is None:
                log.warning("[%s] no exposures in %s window; filter omitted", epoch.epoch_id, molecule)
                continue
            png = render_cmap(img, chem_limits[molecule], CHEMICAL_CMAPS[molecule], size)
            filters[molecule] = uploader.upload_png(f"{slug}_epoch{n}_{molecule.lower()}.png", png)
        epochs_json.append({
            "epoch_id": epoch.epoch_id,
            "date": epoch.when.isoformat().replace("+00:00", "Z"),
            "image_format": "png",
            "image_url": uploader.upload_png(f"{slug}_epoch{n}_rgb.png", render_rgb(epoch, rgb_limits, size)),
            "chemical_filters": filters,
        })

    diff_url = uploader.upload_png(f"{slug}_diff.png", render_difference(diff, size))
    anomalies = detect_anomalies(diff, diff_var, e1, e2, ref_wcs, args)
    log.info("Difference imaging: %d anomalies above %.1f sigma", len(anomalies), args.snr_threshold)

    spec1 = binned_spectrum(target_samples(e1.exposures))
    spec2 = binned_spectrum(target_samples(e2.exposures))
    common_channels = set(spec1) & set(spec2)
    spectrum = sorted(spec1.values(), key=lambda b: b["wavelength_um"])
    log.info("Spectrum: %d/102 channels filled (epoch 1); %d channels shared for light curve",
             len(spectrum), len(common_channels))

    return {
        "target_info": {
            "name": target.name,
            "ra_deg": target.ra_deg,
            "dec_deg": target.dec_deg,
            "fov_arcmin": args.fov,
            "is_cached_golden_path": cached,
        },
        "patch": {
            "width_px": size,
            "height_px": size,
            "wcs": patch_wcs(center.ra.deg, center.dec.deg, args.fov, size, size),
        },
        "timelapse_epochs": epochs_json,
        "difference_imaging": {
            "epoch_a": e2.epoch_id,  # diff = bb2 - bb1
            "epoch_b": e1.epoch_id,
            "image_format": "png",
            "subtracted_image_url": diff_url,
            "detected_anomalies": anomalies,
        },
        "spectrum_102_bands": spectrum,
        "light_curve": [light_curve_point(e1, spec1, common_channels),
                        light_curve_point(e2, spec2, common_channels)],
    }


def generate_target(target: Target, uploader: StorageUploader, args) -> Path:
    t0 = time.monotonic()
    model = SkyPatchResponse.model_validate(build_payload(target, uploader, args))
    out_path = REPO_ROOT / "app" / "data" / target.data_file
    out_path.write_text(json.dumps(model.model_dump(mode="json"), indent=2) + "\n", "utf-8")
    log.info("Wrote %s in %.0fs", out_path.relative_to(REPO_ROOT), time.monotonic() - t0)
    return out_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sel = p.add_mutually_exclusive_group(required=True)
    sel.add_argument("--target", nargs="+", choices=sorted(CATALOG), metavar="KEY",
                     help=f"one or more of: {', '.join(CATALOG)}")
    sel.add_argument("--all", action="store_true", help="process all 5 Golden Path targets")
    p.add_argument("--storage", choices=["local", "supabase"], default="local")
    p.add_argument("--public-base-url", default="http://localhost:8000",
                   help="base URL for --storage local (default: %(default)s)")
    p.add_argument("--fov", type=float, default=5.0, help="field of view in arcmin (default: %(default)s)")
    p.add_argument("--png-size", type=int, default=600, help="output PNG edge in px (default: %(default)s)")
    p.add_argument("--workers", type=int, default=4,
                   help="parallel cutout downloads; IRSA returns 503s above ~6 (default: %(default)s)")
    p.add_argument("--max-exposures-per-epoch", type=int, default=None,
                   help="thin each epoch for a quick smoke test (default: use all)")
    p.add_argument("--epoch-strategy", choices=["six-months", "first-last"], default="six-months",
                   help="six-months: best-covered visit pair ~6 months apart; first-last: earliest vs latest")
    p.add_argument("--epoch-gap-days", type=float, default=30.0, help="gap that separates two visits")
    p.add_argument("--min-exposures", type=int, default=12, help="minimum exposures for a visit to count")
    p.add_argument("--snr-threshold", type=float, default=5.0, help="anomaly threshold in sigma")
    p.add_argument("--min-anomaly-px", type=int, default=2, help="minimum anomaly area in native px")
    p.add_argument("--max-anomalies", type=int, default=10)
    p.add_argument("--dedup-radius-px", type=float, default=12.0,
                   help="merge detections within this distance, in PNG px (default: %(default)s)")
    p.add_argument("--flag-majority", type=float, default=0.5,
                   help="fraction of exposures that must carry a flag at a pixel for it to be reported")
    p.add_argument("--aperture-px", type=float, default=1.5, help="photometry aperture radius (native px)")
    p.add_argument("--annulus-px", type=float, nargs=2, default=(4.0, 7.0), metavar=("R_IN", "R_OUT"))
    p.add_argument("--no-cache", action="store_true", help="don't reuse astropy's download cache")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def default_args(**overrides) -> argparse.Namespace:
    """CLI defaults as a Namespace, for callers that aren't the CLI (live API jobs)."""
    args = parse_args(["--all"])
    for key, value in overrides.items():
        if not hasattr(args, key):
            raise AttributeError(f"unknown pipeline option: {key}")
        setattr(args, key, value)
    return args


def quiet_astropy() -> None:
    astropy_log.setLevel("WARNING")  # silences the per-file SIP/CTYPE notices
    warnings.filterwarnings("ignore", category=FITSFixedWarning)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    quiet_astropy()

    uploader = make_uploader(args.storage, args.public_base_url)
    keys = list(CATALOG) if args.all else args.target
    failed = []
    for key in keys:
        try:
            generate_target(CATALOG[key], uploader, args)
        except Exception:
            log.exception("%s failed", key)
            failed.append(key)
    if failed:
        log.error("Failed targets: %s", ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
