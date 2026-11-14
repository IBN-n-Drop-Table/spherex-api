#!/usr/bin/env python3
"""Offline Golden Path generator for the SPHEREx citizen-science API.

For each target this script:
  1. Queries IRSA SIA for SPHEREx Level 2 Spectral Image MEFs (wide survey,
     falling back to the deep fields when coverage is sparse).
  2. Groups exposures into survey visits by gaps in `t_min` (never fixed
     calendar semesters: those split a visit in two). Every visit becomes a
     timelapse epoch; the difference image compares the first and last.
  3. Downloads a small server-side cutout of every exposure (IRSA cutout
     service: same MEF layout, ~100x fewer bytes than a 66 MB full frame),
     subtracts ZODI from IMAGE, evaluates the spectral WCS at the target,
     measures aperture photometry, and reprojects IMAGE/VARIANCE/FLAGS onto
     a common north-up grid centred on the target at epoch 1.
     The grid covers exactly --fov arcmin; the --png-size PNGs are an exact
     integer upscale of it, described by the `patch.wcs` block in the JSON.
  4. Renders broadband RGB, H2O / CO2 / CO filter and difference PNGs.
     Saturated pixels (OVERFLOW/BLOOM) are shown in the PNGs but excluded
     from every measurement.
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
import hashlib
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
from collections.abc import Callable
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
from astropy.config.paths import get_cache_dir
from astropy.utils.data import conf, is_url_in_cache
from astropy.visualization import AsinhStretch, ZScaleInterval
from astropy.io.votable import parse_single_table
from astropy.wcs import WCS, FITSFixedWarning
from matplotlib import colormaps
from matplotlib.colors import LinearSegmentedColormap
from PIL import Image
import requests
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
from app.core.urls import to_internal, to_relative  # noqa: E402
from app.schemas.sky_patch import DETECTION_LABEL, DETECTION_NOTICE, SkyPatchResponse  # noqa: E402

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
PAIR_SCALE_SIGMA = 5.0  # pairwise difference PNGs saturate at +/- 5 background sigma
PAIR_SKY_BOX_PX = 10  # smooth-sky removal on pairwise differences: 10 grid px = 60" at 5'
PAIR_MIN_PER_DETECTOR = 5  # pairwise: a detector needs >= 5 frames in both visits (see pair_detectors)

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
    "CO": [(4.6, 4.74)],  # CO fundamental band (~4.67 um)
}
CHEMICAL_CMAPS = {
    "H2O": LinearSegmentedColormap.from_list("h2o", ["#000000", "#1f5fff", "#d6ecff"]),
    "CO2": LinearSegmentedColormap.from_list("co2", ["#000000", "#d7301f", "#ffe0c8"]),
    "CO": LinearSegmentedColormap.from_list("co", ["#000000", "#1a9850", "#e5f5c9"]),
}
RGB_DETECTORS = {"R": (5, 6), "G": (3, 4), "B": (1, 2)}

# A detection is "clean" when none of these are set at its centroid.
ARTIFACT_FLAGS = {"HOT", "NONFUNC", "OUTLIER", "SNOWBALL", "BLOOM", "GHOST", "STREAK"}
# Pixels carrying these are excluded from stacks and photometry.
STACK_MASK_FLAGS = ARTIFACT_FLAGS | {"TRANSIENT", "OVERFLOW", "SUR_ERROR", "MISSING_DATA"}
# Bright-star saturation. Verified on real Barnard's Star cutouts (2026-11-14):
# the core carries OVERFLOW + BLOOM + SOURCE. These pixels are SHOWN in the
# PNGs (display only) but EXCLUDED from stacks used for measurements.
SATURATION_FLAGS = {"OVERFLOW", "BLOOM"}
DISPLAY_MASK_FLAGS = STACK_MASK_FLAGS - SATURATION_FLAGS
SATURATED_MESSAGE = ("El núcleo de la fuente satura SPHEREx (flags OVERFLOW/BLOOM): esos píxeles "
                     "se muestran en las imágenes pero se excluyen del espectro y de la curva de luz.")
# Used only if an exposure's FLAGS header carries no MSKNnnnn keywords.
DEFAULT_FLAG_BITS = {0: "TRANSIENT", 19: "OUTLIER", 21: "SOURCE", 26: "BLOOM", 27: "SNOWBALL"}


IRSA_SIA_URL = "https://irsa.ipac.caltech.edu/SIA"
SIA_CACHE_DIR = REPO_ROOT / "cache" / "sia"  # git-ignored; filled by cached (non-live) runs
RETRY_STATUS = {429, 500, 502, 503, 504}


class IrsaUnavailable(RuntimeError):
    """IRSA did not answer in time, or kept answering 429/5xx."""


ProgressFn = Callable[[str, float, str], None]  # (stage, fraction 0..1, message)


def _no_progress(stage: str, fraction: float, message: str) -> None:
    pass


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #
class StorageUploader(ABC):
    """Persists a PNG and returns the public URL the frontend will load."""

    @abstractmethod
    def upload_png(self, filename: str, data: bytes) -> str: ...


class LocalStorage(StorageUploader):
    """Writes under app/static/<url_path>/, which the API serves at /static/<url_path>/.

    Returns a path RELATIVE to the API ('/static/...'); the API prepends its
    public base when serving (app/core/urls.py)."""

    def __init__(self, url_path: str = "golden_path", root: Path | None = None) -> None:
        """`root`: write under <root>/app/static/ instead of the repo (staging runs);
        the returned URLs are the same either way."""
        self.url_path = url_path.strip("/")
        self.directory = (root or REPO_ROOT) / "app" / "static" / self.url_path
        self.directory.mkdir(parents=True, exist_ok=True)

    def upload_png(self, filename: str, data: bytes) -> str:
        (self.directory / filename).write_bytes(data)
        return f"/static/{self.url_path}/{filename}"


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


def make_uploader(kind: str, root: Path | None = None) -> StorageUploader:
    return SupabaseStorage() if kind == "supabase" else LocalStorage(root=root)


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


class CacheMiss(RuntimeError):
    """--offline: something the run needs is not in the local cache."""


def _sia_cache_file(params: list[tuple[str, str]]) -> Path:
    digest = hashlib.sha1(repr(params).encode()).hexdigest()[:20]
    return SIA_CACHE_DIR / f"sia_{digest}.xml"


def _parse_votable(content: bytes) -> Table:
    return parse_single_table(io.BytesIO(content)).to_table(use_names_over_ids=True)


def sia_http(coord: SkyCoord, collections: list[str], budget_s: float,
             cache: bool = False, offline: bool = False) -> Table:
    """SIA2 query straight over HTTP, retried until `budget_s` runs out.

    Why not Irsa.query_sia: pyvo first GETs /SIA/capabilities, and on
    2026-11-14 that endpoint answered 502 about half the time while the query
    endpoint itself worked. Raises IrsaUnavailable when the budget is spent.

    `cache`: keep the raw answer under cache/sia/ (git-ignored) so a later
    `offline` run can replay it without the network. `offline`: answer only
    from that file; raises CacheMiss if it isn't there.
    """
    params = [("COLLECTION", c) for c in collections]
    params += [("POS", f"circle {coord.ra.deg:.7f} {coord.dec.deg:.7f} {1 / 3600:.7f}"),
               ("RESPONSEFORMAT", "VOTABLE")]
    cache_file = _sia_cache_file(params)
    if offline:
        if not cache_file.is_file():
            raise CacheMiss(f"--offline: no cached SIA answer for {', '.join(collections)} at "
                            f"{coord.ra.deg:.5f} {coord.dec.deg:+.5f} ({cache_file.name}). "
                            "Run once without --offline to fetch it.")
        return _parse_votable(cache_file.read_bytes())
    deadline = time.monotonic() + budget_s
    attempt, last = 0, "no answer"
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0.05:
            raise IrsaUnavailable(f"IRSA SIA did not answer within {budget_s:.0f} s ({last})")
        attempt += 1
        try:
            r = requests.get(IRSA_SIA_URL, params=params, timeout=max(0.5, remaining))
            if r.status_code == 200 and b"<VOTABLE" in r.content[:2000]:
                if cache:
                    try:
                        cache_file.parent.mkdir(parents=True, exist_ok=True)
                        cache_file.write_bytes(r.content)
                    except OSError as exc:  # caching is a convenience, never fatal
                        log.warning("could not cache SIA answer: %s", exc)
                return _parse_votable(r.content)
            last = f"HTTP {r.status_code}"
            if r.status_code not in RETRY_STATUS and r.status_code != 200:
                raise IrsaUnavailable(f"IRSA SIA answered {last}")
        except requests.RequestException as exc:
            last = type(exc).__name__
        delay = min(0.5 * 2 ** (attempt - 1), 8.0, max(0.0, deadline - time.monotonic()))
        log.info("SIA attempt %d failed (%s); retrying in %.1fs", attempt, last, delay)
        time.sleep(delay)


def query_sia(coord: SkyCoord, collections: list[str], budget_s: float = 120.0,
              cache: bool = False, offline: bool = False) -> Table:
    rows = sia_http(coord, collections, budget_s, cache=cache, offline=offline)
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


def pick_epochs(groups: list[Table], strategy: str, min_rows: int) -> list[Table] | None:
    """Visits to use, in time order. `all` (default): every usable visit;
    the difference image then compares the first and the last."""
    usable = [g for g in groups if len(g) >= min_rows]
    if len(usable) < 2:
        return None
    if strategy == "all":
        return usable
    if strategy == "first-last":
        return [usable[0], usable[-1]]
    medians = [float(np.median(g["t_min"])) for g in usable]
    best, best_score = None, -1
    for i in range(len(usable)):
        for j in range(i + 1, len(usable)):
            if 120 <= medians[j] - medians[i] <= 240:  # ~6 months
                score = min(len(usable[i]), len(usable[j]))
                if score > best_score:
                    best, best_score = [usable[i], usable[j]], score
    return best or [usable[0], usable[-1]]


def find_epoch_rows(target: Target, args) -> list[Table]:
    conf.remote_timeout = args.download_timeout
    coord = position_at(target, Time("2026-01-01T00:00:00", scale="utc"))
    sia = {"cache": not args.no_cache, "offline": args.offline}
    rows = dedupe(query_sia(coord, WIDE_COLLECTIONS, args.sia_budget_s, **sia), WIDE_COLLECTIONS)
    log.info("SIA wide survey: %d exposures", len(rows))
    visits = pick_epochs(group_epochs(rows, args.epoch_gap_days), args.epoch_strategy, args.min_exposures)
    if visits is None:
        log.info("Wide coverage sparse; adding deep-field collections")
        deep = query_sia(coord, DEEP_COLLECTIONS, args.sia_budget_s, **sia)
        if len(deep):
            rows = dedupe(vstack([rows, deep]) if len(rows) else deep,
                          WIDE_COLLECTIONS + DEEP_COLLECTIONS)
        visits = pick_epochs(group_epochs(rows, args.epoch_gap_days), args.epoch_strategy, args.min_exposures)
    if visits is None:
        raise RuntimeError(
            f"Need two visits with >= {args.min_exposures} exposures each; got "
            f"{[len(g) for g in group_epochs(rows, args.epoch_gap_days)]}"
        )
    return visits


def subsample(rows: Table, limit: int | None) -> Table:
    """Evenly thin an epoch while keeping every detector represented."""
    if not limit or len(rows) <= limit:
        return rows
    rows = rows.copy()
    rows.sort(["energy_bandpassname", "t_min"])
    return rows[np.unique(np.linspace(0, len(rows) - 1, limit).astype(int))]


def visit_ids(visits: list[Table]) -> tuple[list[Time], list[str]]:
    """Median time and epoch_id of each visit ('2025_M0', '2025_M7'...: months since the first)."""
    times = [Time(float(np.median(v["t_min"])), format="mjd", scale="utc") for v in visits]
    if not times:
        return [], []
    t1 = times[0]
    return times, [f"{t1.datetime.year}_M{round((t - t1).to_value(u.day) / 30.44)}" for t in times]


def summarize_visits(visits: list[Table]) -> list[dict]:
    """Metadata-only epoch list for GET /api/v1/epochs."""
    times, ids = visit_ids(visits)
    out = []
    for v, t, eid in zip(visits, times, ids):
        mjd = np.asarray(v["t_min"], dtype=float)
        bands = [str(b) for b in v["energy_bandpassname"]]
        out.append({
            "epoch_id": eid,
            "date": t.datetime.replace(microsecond=0).isoformat() + "Z",
            "date_start": Time(mjd.min(), format="mjd").iso[:10],
            "date_end": Time(mjd.max(), format="mjd").iso[:10],
            "n_images": len(v),
            "n_per_detector": {f"D{d}": sum(b.endswith(f"D{d}") for b in bands) for d in range(1, 7)},
        })
    return out


def subsample_per_detector(rows: Table, per_detector: int | None) -> Table:
    """Keep at most `per_detector` exposures per detector, spread in time."""
    if not per_detector or not len(rows):
        return rows
    keep = []
    for band in sorted(set(str(b) for b in rows["energy_bandpassname"])):
        idx = np.where(np.asarray([str(b) for b in rows["energy_bandpassname"]]) == band)[0]
        idx = idx[np.argsort(np.asarray(rows["t_min"])[idx])]
        pick = np.unique(np.linspace(0, len(idx) - 1, min(per_detector, len(idx))).round().astype(int))
        keep.extend(idx[pick].tolist())
    out = rows[sorted(keep)]
    out.sort("t_min")
    return out


def open_fits_with_retry(url: str, use_cache: bool, base_delay: float = 5.0) -> fits.HDUList:
    return with_retry(lambda: fits.open(url, memmap=False, cache=use_cache),
                      what=f"GET {url[-60:]}", base_delay=base_delay)


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
    display: np.ndarray  # same, but saturated pixels (OVERFLOW/BLOOM) kept: PNGs only
    variance: np.ndarray  # on reference grid
    flags: np.ndarray  # raw bitmask on reference grid (float, NaN = no data)
    wave_map: np.ndarray  # LVF wavelength (um) at each reference-grid pixel
    bw_map: np.ndarray  # LVF bandwidth (um) at each reference-grid pixel
    flag_names: dict[int, str]
    ap_flux: float | None  # target photometry, measured on the native cutout
    ap_var: float | None
    saturated: bool = False  # OVERFLOW/BLOOM inside the target aperture


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


def cutout_request(row, target: Target, fov_arcmin: float) -> tuple[str, SkyCoord]:
    """(IRSA cutout URL, target position at the exposure date). The URL is also
    the astropy download-cache key, so it must stay byte-for-byte stable."""
    when = Time(float(row["t_min"]), format="mjd", scale="utc")
    coord = position_at(target, when)
    support_arcmin = fov_arcmin * math.sqrt(2) + 1.5  # covers a rotated fov + annulus
    url = (f"{row['access_url']}?center={coord.ra.deg:.6f},{coord.dec.deg:.6f}"
           f"&size={support_arcmin / 60:.4f}")
    return url, coord


def check_cutout_cache(visits: list[Table], target: Target, args) -> tuple[list[Table], int, int]:
    """(visits, cached, total). With --offline, missing cutouts raise CacheMiss,
    unless --skip-missing drops them (same as a download that failed)."""
    total = sum(len(v) for v in visits)
    if args.no_cache:
        return visits, 0, total
    keep = [np.array([is_url_in_cache(cutout_request(row, target, args.fov)[0]) for row in v], bool)
            for v in visits]
    missing = total - int(sum(k.sum() for k in keep))
    if missing and args.offline:
        if not args.skip_missing:
            raise CacheMiss(f"--offline: {missing}/{total} cutouts of {target.name} are not in the "
                            f"astropy cache ({get_cache_dir()}). Run without --offline to download "
                            "only the missing ones, or add --skip-missing to leave them out.")
        log.warning("--offline --skip-missing: leaving out %d uncached cutout(s)", missing)
        visits = [v[k] for v, k in zip(visits, keep)]
    return visits, total - missing, total


def process_row(row, target: Target, ref_wcs: WCS, ref_shape: tuple[int, int], args) -> Exposure | None:
    url, coord = cutout_request(row, target, args.fov)

    with open_fits_with_retry(url, use_cache=not args.no_cache, base_delay=args.retry_base_delay) as hdulist:
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
    display_bits = sum(1 << b for b, n in flag_names.items() if n in DISPLAY_MASK_FLAGS)
    sat_bits = sum(1 << b for b, n in flag_names.items() if n in SATURATION_FLAGS)
    yy, xx = np.indices(clean.shape)
    in_aperture = np.hypot(xx - x, yy - y) <= args.aperture_px + 0.5
    saturated = bool(((flags_raw[in_aperture] & sat_bits) != 0).any())

    # Display copy: saturated pixels stay visible even when other bits
    # (TRANSIENT, SUR_ERROR) ride along, as they do around bright stars. Core
    # pixels that IRSA itself left as NaN with OVERFLOW are painted at the
    # cutout maximum: the usual "saturated = white" rendering, display only.
    overflow_bits = sum(1 << b for b, n in flag_names.items() if n == "OVERFLOW")
    overflow = ((flags_raw & overflow_bits) != 0).astype(np.uint8)
    near_core = cv2.dilate(overflow, np.ones((7, 7), np.uint8)).astype(bool)  # <= 3 px from OVERFLOW
    is_sat = ((flags_raw & sat_bits) != 0) & near_core  # stray BLOOM elsewhere stays masked
    display = clean.copy()
    display[((flags_raw & display_bits) != 0) & ~is_sat] = np.nan
    finite = np.isfinite(display)
    if finite.any():
        display[is_sat & ~finite] = float(np.nanmax(display))
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
                            (flags_raw.astype(np.float64), "nearest-neighbor"), (display, "bilinear")):
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
        display=reprojected[3],
        variance=reprojected[1],
        flags=reprojected[2],
        wave_map=wave_map,
        bw_map=bw_map,
        flag_names=flag_names,
        ap_flux=ap_flux,
        ap_var=ap_var,
        saturated=saturated,
    )


class DownloadProgress:
    """Thread-safe cutout counter that reports to a ProgressFn (5% -> 85%)."""

    def __init__(self, total: int, workers: int, progress: ProgressFn, seconds_per_cutout: float):
        import threading

        self.total, self.done = max(total, 1), 0
        self.workers, self.spc = max(workers, 1), seconds_per_cutout
        self.progress, self.t0 = progress, time.monotonic()
        self._lock = threading.Lock()

    def tick(self) -> None:
        with self._lock:
            self.done += 1
            done = self.done
        elapsed = time.monotonic() - self.t0
        rate = elapsed / done  # observed seconds per cutout (all workers)
        remaining = max(0, round(rate * (self.total - done)))
        self.progress("download", 0.05 + 0.80 * done / self.total,
                      f"Descargando recortes de IRSA: {done}/{self.total} (~{remaining} s restantes)")


def process_rows(rows: Table, target: Target, ref_wcs: WCS, ref_shape, args, label: str,
                 counter: DownloadProgress | None = None) -> list[Exposure]:
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
            if counter is not None:
                counter.tick()
            if n % 20 == 0 or n == len(futures):
                log.info("[%s] %d/%d exposures processed", label, n, len(futures))
    log.info("[%s] %d usable, %d failed, %d off-target", label, len(out), failures,
             len(rows) - len(out) - failures)
    if len(rows) and failures > len(rows) / 2:
        # Mostly download errors: treat as an IRSA outage, not as "no data".
        raise IrsaUnavailable(f"{failures}/{len(rows)} cutout downloads failed for {label}")
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
    display_stacks: dict[int, tuple[np.ndarray, np.ndarray]] = field(default_factory=dict)
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
            prod.display_stacks[det] = median_stack([e.display for e in members],
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

    def stack_of(self, detectors, display: bool = False) -> tuple[np.ndarray, np.ndarray] | None:
        source = self.display_stacks if display else self.detector_stacks
        stacks = [source[d] for d in detectors if d in source]
        return mean_of(stacks) if stacks else None

    def chemical_stack(self, molecule: str, display: bool = True) -> np.ndarray | None:
        """Stack of the exposures whose wavelength at the target falls in the window."""
        members = [e for e in self.exposures
                   if any(lo <= e.wavelength_um <= hi for lo, hi in CHEMICAL_WINDOWS_UM[molecule])]
        if not members:
            return None
        return median_stack([e.display if display else e.image for e in members],
                            [e.variance for e in members])[0]

    @property
    def saturated(self) -> bool:
        covering = [e for e in self.exposures if e.ap_flux is not None or e.saturated]
        return bool(covering) and sum(e.saturated for e in covering) > len(covering) / 2

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
    channels = {c: epoch.stack_of(dets, display=True) for c, dets in RGB_DETECTORS.items()}
    fallback = epoch.stack_of(LVF_BANDS, display=True)[0]
    planes = [asinh_stretch(channels[c][0] if channels[c] else fallback, limits[c]) for c in "RGB"]
    return to_png(np.dstack(planes), size)


def render_cmap(img: np.ndarray, limits: tuple[float, float], cmap, size: int) -> bytes:
    rgba = cmap(asinh_stretch(img, limits))
    return to_png(rgba[..., :3], size)


def render_difference(diff: np.ndarray, size: int, lim: float | None = None) -> bytes:
    """Red = brighter in the later epoch, blue = fainter, black = no data.

    `lim` (colour saturation, MJy/sr): default = 99.5th percentile of |diff|,
    the first-vs-last rendering. Pairwise images pass a background-based limit."""
    if lim is None:
        finite = diff[np.isfinite(diff)]
        lim = float(np.percentile(np.abs(finite), 99.5)) if finite.size else 1.0
    lim = lim or 1.0
    rgba = colormaps["RdBu_r"]((np.clip(diff / lim, -1, 1) + 1) / 2)
    rgba[~np.isfinite(diff)] = (0, 0, 0, 1)
    return to_png(rgba[..., :3], size)


def background_sigma(diff: np.ndarray) -> float | None:
    """Robust (3-sigma-clipped) std of a difference image. Saturated and masked
    pixels are NaN in the clean stacks, so they never enter it."""
    finite = diff[np.isfinite(diff)]
    if finite.size < 10:
        return None
    std = float(sigma_clipped_stats(finite, sigma=3.0)[2])
    return std if std > 0 and math.isfinite(std) else None


def sky_background(diff: np.ndarray, stacks: list[np.ndarray], box: int = PAIR_SKY_BOX_PX) -> np.ndarray:
    """Smooth sky left in a difference (zodi/scattered-light gradients).

    Median of `box`-px blocks after masking every source of either epoch (> 3
    sigma, dilated 2 px, so star wings and haloes stay out of it), bilinearly
    interpolated back. `box` (60" at 5') is far larger than the PSF (~2 px), so
    point sources and dipoles are untouched."""
    mask = ~np.isfinite(diff)
    for img in stacks:
        finite = img[np.isfinite(img)]
        if finite.size < 10:
            continue
        _, med, std = sigma_clipped_stats(finite, sigma=3.0)
        if std > 0:
            mask |= np.nan_to_num(np.abs(img - med) / std, nan=0.0) > 3
    mask = cv2.dilate(mask.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
    work = np.where(mask, np.nan, diff)
    n_y, n_x = diff.shape
    ny, nx = max(1, round(n_y / box)), max(1, round(n_x / box))
    nodes = np.full((ny, nx), np.nan)
    for j in range(ny):
        for i in range(nx):
            blk = work[j * n_y // ny:(j + 1) * n_y // ny, i * n_x // nx:(i + 1) * n_x // nx]
            if np.isfinite(blk).sum() >= blk.size / 4:
                nodes[j, i] = np.nanmedian(blk)
    if not np.isfinite(nodes).any():
        return np.zeros_like(diff)
    nodes = np.where(np.isfinite(nodes), nodes, np.nanmedian(nodes))
    return cv2.resize(nodes.astype(np.float32), (n_x, n_y), interpolation=cv2.INTER_LINEAR).astype(np.float64)


# --------------------------------------------------------------------------- #
# Epoch differences
# --------------------------------------------------------------------------- #
def expected_shift_arcsec(target: Target, t_from: datetime, t_to: datetime) -> float:
    """How far the target moves between two dates (catalogue proper motion)."""
    a = position_at(target, Time(t_from))
    b = position_at(target, Time(t_to))
    return float(a.separation(b).arcsec)


def difference_meta(target: Target, earlier: EpochProduct, later: EpochProduct,
                    detectors: list[int]) -> dict:
    """Metadata shared by the first-vs-last difference and every pairwise one."""
    shift = expected_shift_arcsec(target, earlier.when, later.when)
    return {
        "delta_t_days": round((later.when - earlier.when).total_seconds() / 86400.0, 2),
        "detectors_used": list(detectors),
        "expected_shift_arcsec": round(shift, 2),
        "weak_signal": shift < PIXEL_SCALE_ARCSEC,
    }


def pair_detectors(earlier: EpochProduct, later: EpochProduct) -> list[int]:
    """Detectors both visits share, keeping only those with at least
    PAIR_MIN_PER_DETECTOR exposures in each: a median of 3 frames does not reject
    artefacts (measured: Epsilon Indi B M0 has 3 detector-1 frames and that
    detector's difference is 18x noisier than its formal error). If none
    qualifies (live jobs take 1 frame per detector), every shared detector is used."""
    shared = sorted(set(earlier.detector_stacks) & set(later.detector_stacks))

    def count(ep: EpochProduct, det: int) -> int:
        return sum(e.detector == det for e in ep.exposures)

    deep = [d for d in shared if min(count(earlier, d), count(later, d)) >= PAIR_MIN_PER_DETECTOR]
    return deep or shared


def pairwise_differences(epochs: list[EpochProduct], target: Target, uploader: StorageUploader,
                         slug: str, size: int) -> list[dict]:
    """Consecutive-visit differences (later - earlier), from the per-epoch stacks
    already built: no re-stacking, no downloads, no anomaly detection.

    Each pair uses the detectors THOSE two visits share (pair_detectors). A smooth sky
    (sky_background) is removed, then the colour scale is +/- PAIR_SCALE_SIGMA x
    the robust background sigma of the clean difference (saturated/masked
    pixels excluded), so the field never washes out to white.
    With fewer than 3 visits the only pair is the first-vs-last one: empty list.
    """
    if len(epochs) < 3:
        return []
    out = []
    for i in range(len(epochs) - 1):
        earlier, later = epochs[i], epochs[i + 1]
        dets = pair_detectors(earlier, later)
        if not dets:
            log.warning("[%s vs %s] no shared detector; pair skipped", later.epoch_id, earlier.epoch_id)
            continue
        before, after = earlier.stack_of(dets)[0], later.stack_of(dets)[0]
        sky = sky_background(after - before, [before, after])
        clean = after - before - sky
        shown = later.stack_of(dets, display=True)[0] - earlier.stack_of(dets, display=True)[0] - sky
        sigma = background_sigma(clean)
        lim = PAIR_SCALE_SIGMA * sigma if sigma else None
        name = f"{slug}_diff_epoch{i + 1}_epoch{i + 2}.png"
        out.append({
            "kind": "consecutive",
            "epoch_a": later.epoch_id,
            "epoch_b": earlier.epoch_id,
            "image_format": "png",
            "subtracted_image_url": uploader.upload_png(name, render_difference(shown, size, lim)),
            **difference_meta(target, earlier, later, dets),
        })
        log.info("Pair %s - %s: detectors %s, background sigma %.4g MJy/sr", later.epoch_id,
                 earlier.epoch_id, dets, sigma or float("nan"))
    return out


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
            "kind": "appeared" if sign > 0 else "disappeared",
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
    """Mean over the channels every epoch shares. No data -> null + reason, never 0."""
    point = {"epoch_id": epoch.epoch_id, "date": epoch.when.date().isoformat(),
             "flux_mjy_sr": None, "variance": None, "note": None}
    if not spec:
        point["note"] = ("Sin fotometría válida: el núcleo satura SPHEREx (OVERFLOW/BLOOM)"
                         if epoch.saturated else "Sin fotometría válida en el centro en esta visita")
        return point
    keys = common & set(spec)
    if not keys:
        keys = set(spec)
        point["note"] = "Sin canales comunes con las otras visitas: media de sus propios canales (aproximada)"
    fluxes = [spec[k]["flux_mjy_sr"] for k in keys]
    variances = [spec[k]["variance"] for k in keys]
    point["flux_mjy_sr"] = sig(float(np.mean(fluxes)))
    point["variance"] = sig(float(np.sum(variances) / len(variances) ** 2))
    return point


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def build_payload(target: Target, uploader: StorageUploader, args, cached: bool = True,
                  progress: ProgressFn | None = None) -> dict:
    """Run the whole pipeline for one position; return a SkyPatchResponse-shaped dict
    (image URLs relative to the API, see app/core/urls.py)."""
    progress = progress or _no_progress
    log.info("=== %s (%s) ===", target.name, target.key)
    progress("query", 0.02, "Consultando el archivo de IRSA (metadatos SPHEREx)...")
    visits = find_epoch_rows(target, args)
    times, ids = visit_ids(visits)  # from the FULL visits, so /epochs and sky-patch agree
    visits = [subsample_per_detector(subsample(v, args.max_exposures_per_epoch), args.max_per_detector)
              for v in visits]
    visits, n_cached, n_listed = check_cutout_cache(visits, target, args)
    n_cutouts = sum(len(v) for v in visits)
    if not args.no_cache:
        log.info("Cutouts: %d/%d already in the astropy cache, %d to download",
                 n_cached, n_listed, n_cutouts - n_cached)
    eta = round(math.ceil(n_cutouts / max(args.workers, 1)) * args.seconds_per_cutout + 10)
    progress("download", 0.05,
             f"{len(visits)} visitas de SPHEREx encontradas. Descargando {n_cutouts} recortes de IRSA "
             f"(~{eta} s estimados). Procesamiento en vivo: tarda, no es instantáneo.")

    t1 = times[0]
    for eid, t, v in zip(ids, times, visits):
        log.info("Visit %s: %s, %d exposures", eid, t.iso[:10], len(v))

    center = position_at(target, t1)
    ref_wcs, ref_shape = build_reference_wcs(center, args.fov, args.png_size)
    log.info("Reference grid %dx%d px centred on %.6f, %.6f (epoch-1 position)",
             *ref_shape, center.ra.deg, center.dec.deg)

    counter = DownloadProgress(n_cutouts, args.workers, progress, args.seconds_per_cutout)
    epochs = [EpochProduct.build(eid, process_rows(v, target, ref_wcs, ref_shape, args, eid, counter))
              for eid, v in zip(ids, visits)]
    progress("stack", 0.88, "Apilando por visita y detector...")

    # Difference: first vs last visit (longest baseline = most motion). Broadband
    # = mean of per-detector median stacks over the detectors both visits share,
    # so each detector is only ever subtracted from itself (each sees other wavelengths).
    e1, e2 = epochs[0], epochs[-1]
    common_dets = sorted(set(e1.detector_stacks) & set(e2.detector_stacks))
    if not common_dets:
        raise RuntimeError("First and last visits share no detectors; cannot difference")
    bb1, var1 = e1.stack_of(common_dets)
    bb2, var2 = e2.stack_of(common_dets)
    diff, diff_var = bb2 - bb1, var1 + var2
    # The PNG uses the display stacks (saturated cores visible); detection uses the clean ones.
    diff_display = e2.stack_of(common_dets, display=True)[0] - e1.stack_of(common_dets, display=True)[0]

    progress("render", 0.93, "Generando imágenes, resta y espectro...")
    size = args.png_size
    slug = target.slug
    epochs_json = []
    # Stretch limits come from the CLEAN stacks, so a saturated core clips to
    # white instead of crushing the rest of the field to black.
    rgb_limits = {c: display_limits(*(s[0] for e in epochs if (s := e.stack_of(d))))
                  for c, d in RGB_DETECTORS.items()}
    chem = {m: [e.chemical_stack(m) for e in epochs] for m in CHEMICAL_WINDOWS_UM}
    chem_limits = {m: display_limits(*(e.chemical_stack(m, display=False) for e in epochs))
                   for m, imgs in chem.items() if any(i is not None for i in imgs)}

    for n, epoch in enumerate(epochs, 1):
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
            "n_exposures": len(epoch.exposures),
        })

    diff_url = uploader.upload_png(f"{slug}_diff.png", render_difference(diff_display, size))
    anomalies = detect_anomalies(diff, diff_var, e1, e2, ref_wcs, args)
    log.info("Difference imaging: %d anomalies above %.1f sigma", len(anomalies), args.snr_threshold)
    if len(epochs) >= 3:
        progress("difference", 0.95, "Calculando restas entre épocas consecutivas...")
    pairwise = pairwise_differences(epochs, target, uploader, slug, size)

    specs = [binned_spectrum(target_samples(e.exposures)) for e in epochs]
    common_channels = set.intersection(*(set(sp) for sp in specs if sp)) if any(specs) else set()
    # Target spectrum: the visit with the most channels (ties: the earliest).
    best = max(range(len(epochs)), key=lambda i: (len(specs[i]), -i))
    spectrum = sorted(specs[best].values(), key=lambda b: b["wavelength_um"])
    saturated = any(e.saturated for e in epochs)
    if not spectrum:
        message = SATURATED_MESSAGE if saturated else "Sin fotometría válida en el centro del parche."
    elif saturated:
        message = SATURATED_MESSAGE
    else:
        message = None
    log.info("Spectrum: %d/102 channels (visit %s); %d channels shared by all visits; saturated=%s",
             len(spectrum), epochs[best].epoch_id, len(common_channels), saturated)

    progress("render", 0.99, "Listo.")
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
            "epoch_a": e2.epoch_id,  # diff = last - first
            "epoch_b": e1.epoch_id,
            "image_format": "png",
            "subtracted_image_url": diff_url,
            "detected_anomalies": anomalies,
            "detection_label": DETECTION_LABEL,
            "detection_notice": DETECTION_NOTICE,
            "kind": "first_vs_last",
            **difference_meta(target, e1, e2, common_dets),
            "pairwise": pairwise,
        },
        "spectrum_102_bands": spectrum,
        "spectrum_epoch_id": epochs[best].epoch_id,
        "light_curve": [light_curve_point(e, sp, common_channels) for e, sp in zip(epochs, specs)],
        "saturated": saturated,
        "spectrum_message": message,
    }


def generate_target(target: Target, uploader: StorageUploader, args) -> Path:
    t0 = time.monotonic()
    model = SkyPatchResponse.model_validate(to_internal(build_payload(target, uploader, args)))
    out_path = (args.output_root or REPO_ROOT) / "app" / "data" / target.data_file
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = to_relative(model.model_dump(mode="json"))  # relative image paths on disk
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", "utf-8")
    log.info("Wrote %s in %.0fs", out_path, time.monotonic() - t0)
    return out_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sel = p.add_mutually_exclusive_group(required=True)
    sel.add_argument("--target", nargs="+", choices=sorted(CATALOG), metavar="KEY",
                     help=f"one or more of: {', '.join(CATALOG)}")
    sel.add_argument("--all", action="store_true", help="process all 5 Golden Path targets")
    p.add_argument("--storage", choices=["local", "supabase"], default="local")
    p.add_argument("--fov", type=float, default=5.0, help="field of view in arcmin (default: %(default)s)")
    p.add_argument("--png-size", type=int, default=600, help="output PNG edge in px (default: %(default)s)")
    p.add_argument("--workers", type=int, default=4,
                   help="parallel cutout downloads; IRSA returns 503s above ~6 (default: %(default)s)")
    p.add_argument("--max-exposures-per-epoch", type=int, default=None,
                   help="thin each epoch for a quick smoke test (default: use all)")
    p.add_argument("--max-per-detector", type=int, default=None,
                   help="at most N exposures per detector and visit (live mode uses 1)")
    p.add_argument("--epoch-strategy", choices=["all", "six-months", "first-last"], default="all",
                   help="all: every visit in the timelapse, difference = first vs last (default); "
                        "six-months / first-last: only two visits")
    p.add_argument("--sia-budget-s", type=float, default=120.0,
                   help="total seconds (retries included) for each IRSA SIA query")
    p.add_argument("--download-timeout", type=float, default=120.0, help="per-cutout HTTP timeout (s)")
    p.add_argument("--retry-base-delay", type=float, default=5.0,
                   help="first back-off delay after a 429/5xx cutout error (doubles each retry)")
    p.add_argument("--seconds-per-cutout", type=float, default=12.0,
                   help="used only for the ETA message (measured ~11-13 s on 2026-11-14)")
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
    p.add_argument("--offline", action="store_true", default=os.getenv("CACHE_ONLY") == "1",
                   help="use only cached SIA answers (cache/sia/) and cached cutouts (astropy cache); "
                        "fail with a clear message if anything is missing. Also: CACHE_ONLY=1")
    p.add_argument("--skip-missing", action="store_true",
                   help="with --offline: leave uncached cutouts out (logged) instead of failing")
    p.add_argument("--output-root", type=Path, default=None,
                   help="write app/data/*.json and app/static/golden_path/*.png under this directory "
                        "instead of the repo (staging; image URLs are unchanged)")
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

    if args.offline and args.no_cache:
        log.error("--offline needs the cache: drop --no-cache")
        return 2
    if args.offline:
        conf.allow_internet = False  # belt and braces: astropy must not reach the network
    uploader = make_uploader(args.storage, args.output_root)
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
