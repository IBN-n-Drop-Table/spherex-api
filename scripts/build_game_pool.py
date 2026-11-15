#!/usr/bin/env python3
"""Offline builder of the "find what moves" round pool: WISE (2010) vs SPHEREx (2025-26).

STATUS (2026-11-15): the SPHEREx half is NOT validated. The validation run (10 new
stars) was blocked by IRSA: SIA answered HTTP 502 to every attempt for 30 min, so no
SPHEREx image was downloaded or checked. Validated so far: the Gaia candidate query,
the Gaia isolation check and the unWISE download + QA. See docs/game-pool.md.

One round = one nearby high proper-motion star, seen by WISE around 2010 and by
SPHEREx in its latest visit. Over ~15 years such a star moves 3 to 25+ SPHEREx
pixels, which a blink shows plainly (viability/README.md: a live SPHEREx-vs-SPHEREx
round moves < 1 pixel and takes 94-141 s).

The ANSWER is always Gaia DR3 (position + proper motion) propagated to the two
image dates and projected with the round's WCS, never a detection. The images are
only checked (QA) to show the star where Gaia says, and nothing else there.

Steps
  candidates  Gaia DR3 stars with parallax > --min-parallax mas and total proper
              motion >= --min-pm mas/yr (so the expected WISE->SPHEREx shift is
              >= 3 SPHEREx pixels), |b| >= 15 deg. Bounded query on the ARI
              Heidelberg Gaia mirror (parallax is indexed there; the ESA archive
              was unreachable on 2026-11-15). Writes cache/game_pool/candidates.json.
  build       For each candidate (resumable; cache/game_pool/build_log.jsonl):
                1. isolation in Gaia: no source brighter than G_star + 2.5 within
                   15" of the star's position at either date;
                2. WISE: unWISE "allwise" W1 cutout (unwise.me, Lang 2014; the
                   original WISE mission only, 2010-01..2011-02, epoch taken as
                   2010.5), reprojected onto the round grid;
                3. SPHEREx: IRSA SIA + cutouts of the latest visit (k exposures per
                   detector), stacked per detector and averaged (the Golden Path
                   pipeline's own functions), reprojected onto the same grid;
                4. QA on both images: star visible (peak >= 5 sigma), centroid
                   within tolerance of the Gaia prediction, and the other date's
                   position empty (no blend or static source there);
                5. two grayscale PNGs named by a salted hash, and a manifest
                   entry with the answer (server side only).
              Writes app/static/game_pool/*.png and app/data/game_pool.json.

The answer window: each round is a --png-size square grid; the grid centre is moved
off the path's midpoint by a random (seeded by the star) offset, so the answer is
not always the centre.

Usage:
  python scripts/build_game_pool.py candidates
  python scripts/build_game_pool.py build [--limit N] [--stars ID ...] [--out-root DIR]
  python scripts/build_game_pool.py manifest      # rewrite the manifest from the log
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import math
import os
import random
import sys
import tarfile
import time
import warnings
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

SCRIPTS = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPTS))

log = logging.getLogger("game_pool")

GAIA_TAP = os.getenv("GAIA_TAP_URL", "https://gaia.ari.uni-heidelberg.de/tap")
SIMBAD_TAP = os.getenv("SIMBAD_TAP_URL", "https://simbad.cfa.harvard.edu/simbad/sim-tap/sync")
UNWISE_CUTOUT = "https://unwise.me/cutout_fits"
UNWISE_PIXEL_ARCSEC = 2.75
WISE_EPOCH_YR = 2010.5  # unWISE allwise: 2010-01..2011-02, no per-pixel epoch in the cutout
WISE_DATE = "2010-07-02T00:00:00Z"
GAIA_EPOCH_YR = 2016.0
SPHEREX_PIXEL_ARCSEC = 6.15
WORK_DIR = REPO_ROOT / "cache" / "game_pool"  # git-ignored
GOLDEN_SOURCE_IDS = {4472832130942575872, 6412596012146801152}  # Barnard's Star, Epsilon Indi B

# Difficulty by expected shift in native SPHEREx pixels.
DIFFICULTY_PX = (("easy", 10.0), ("medium", 5.0), ("hard", 3.0))


# --------------------------------------------------------------------------- #
# Geometry
# --------------------------------------------------------------------------- #


def gaia_at(star: dict, year: float) -> tuple[float, float]:
    """Linear proper-motion propagation from Gaia DR3 (epoch 2016.0); parallax ignored (< 0.3")."""
    dt = year - GAIA_EPOCH_YR
    dec = star["dec"] + star["pmdec"] / 3.6e6 * dt
    ra = star["ra"] + star["pmra"] / 3.6e6 * dt / math.cos(math.radians(star["dec"]))
    return ra % 360.0, dec


def sep_arcsec(a: tuple[float, float], b: tuple[float, float]) -> float:
    d1, d2, da = math.radians(a[1]), math.radians(b[1]), math.radians(a[0] - b[0])
    c = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(da)
    return math.degrees(math.acos(max(-1.0, min(1.0, c)))) * 3600.0


def offset_position(ra: float, dec: float, east_arcsec: float, north_arcsec: float) -> tuple[float, float]:
    return ((ra + east_arcsec / 3600.0 / math.cos(math.radians(dec))) % 360.0, dec + north_arcsec / 3600.0)


def grid_wcs(ra: float, dec: float, size_px: int, scale_arcsec: float):
    from astropy.wcs import WCS

    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = [ra, dec]
    w.wcs.crpix = [(size_px + 1) / 2, (size_px + 1) / 2]
    w.wcs.cdelt = [-scale_arcsec / 3600.0, scale_arcsec / 3600.0]
    w.wcs.cunit = ["deg", "deg"]
    return w


def to_png_xy(wcs, size_px: int, pos: tuple[float, float]) -> tuple[float, float]:
    """(ra, dec) -> continuous PNG position (pixel i spans [i, i+1), origin top-left, +y down),
    the convention of app/schemas/game.py."""
    from astropy.coordinates import SkyCoord

    x0, y0 = (float(v) for v in wcs.world_to_pixel(SkyCoord(*pos, unit="deg")))  # 0-based, +y up
    return x0 + 0.5, size_px - y0 - 0.5


def difficulty_of(shift_native_px: float) -> str | None:
    for name, floor in DIFFICULTY_PX:
        if shift_native_px >= floor:
            return name
    return None


# --------------------------------------------------------------------------- #
# Catalogues
# --------------------------------------------------------------------------- #


def tap_sync(url: str, adql: str):
    """Synchronous TAP query (pyvo 1.10: run_sync has no timeout; extra keywords become TAP params)."""
    import pyvo
    import requests

    session = requests.Session()
    session.headers["User-Agent"] = "spherex-citizen-science-pool-builder"
    return pyvo.dal.TAPService(url, session=session).run_sync(adql, maxrec=100000).to_table()


def gaia_cone(ra: float, dec: float, radius_arcsec: float) -> list[dict]:
    """Gaia DR3 sources in a cone, as source_id ranges: source_id // 2**35 is the HEALPix
    level-12 nested pixel (Gaia DR3 docs). On the ARI mirror source_id is indexed while a
    CONTAINS() on ra/dec was not answered within 100 s (2026-11-15)."""
    from astropy import units as u
    from astropy.coordinates import Latitude, Longitude
    from astropy_healpix import HEALPix

    pix = HEALPix(nside=2**12, order="nested").cone_search_lonlat(
        Longitude(ra * u.deg), Latitude(dec * u.deg), radius_arcsec * u.arcsec)
    ranges = " OR ".join(f"source_id BETWEEN {int(p) * 2**35} AND {(int(p) + 1) * 2**35 - 1}" for p in pix)
    t = tap_sync(GAIA_TAP, "SELECT source_id, ra, dec, pmra, pmdec, phot_g_mean_mag "
                           f"FROM gaiadr3.gaia_source WHERE {ranges}")
    out = []
    for r in t:
        row = {"source_id": int(r["source_id"]), "ra": float(r["ra"]), "dec": float(r["dec"])}
        for k, default in (("pmra", 0.0), ("pmdec", 0.0), ("phot_g_mean_mag", 99.0)):
            row[k] = default if np.ma.is_masked(r[k]) else float(r[k])
        if sep_arcsec((ra, dec), (row["ra"], row["dec"])) <= radius_arcsec:
            out.append(row)
    return out


def simbad_name(star: dict) -> str | None:
    """Best-effort common identifier (SIMBAD, J2000 position), most-cited object within 5"."""
    try:
        from app.services.simbad import http_transport

        ra, dec = gaia_at(star, 2000.0)
        adql = ("SELECT TOP 1 main_id FROM basic WHERE CONTAINS(POINT('ICRS', ra, dec), "
                f"CIRCLE('ICRS', {ra:.6f}, {dec:.6f}, {5 / 3600:.6f})) = 1 ORDER BY nbref DESC")
        payload = http_transport(SIMBAD_TAP, adql, 15.0)
        return str(payload["data"][0][0]).strip() if payload.get("data") else None
    except Exception as exc:  # noqa: BLE001 - a name is a nicety
        log.info("SIMBAD name lookup failed: %s", exc)
        return None


def cmd_candidates(a: argparse.Namespace) -> int:
    adql = ("SELECT source_id, ra, dec, l, b, pm, pmra, pmdec, parallax, ruwe, phot_g_mean_mag "
            f"FROM gaiadr3.gaia_source WHERE parallax > {a.min_parallax} AND pm >= {a.min_pm}")
    t0 = time.monotonic()
    t = tap_sync(GAIA_TAP, adql)
    log.info("Gaia: %d rows in %.1f s", len(t), time.monotonic() - t0)
    rows = []
    for r in t:
        if abs(float(r["b"])) < a.min_abs_b or np.ma.is_masked(r["phot_g_mean_mag"]):
            continue
        sid = int(r["source_id"])
        if sid in GOLDEN_SOURCE_IDS:
            continue
        rows.append({k: (int(r[k]) if k == "source_id" else float(r[k])) for k in
                     ("source_id", "ra", "dec", "l", "b", "pm", "pmra", "pmdec", "parallax", "phot_g_mean_mag")}
                    | {"ruwe": None if np.ma.is_masked(r["ruwe"]) else float(r["ruwe"])})
    random.Random(a.seed).shuffle(rows)
    a.work.mkdir(parents=True, exist_ok=True)
    (a.work / "candidates.json").write_text(json.dumps({
        "query": adql, "service": GAIA_TAP, "min_abs_b": a.min_abs_b,
        "created": datetime.now(UTC).isoformat(timespec="seconds"), "stars": rows}, indent=1))
    log.info("%d candidates (|b| >= %.0f) -> %s", len(rows), a.min_abs_b, a.work / "candidates.json")
    return 0


# --------------------------------------------------------------------------- #
# Images
# --------------------------------------------------------------------------- #


def unwise_w1(ra: float, dec: float, size_px: int, timeout: float) -> tuple[np.ndarray, object]:
    """W1 image + WCS from the unWISE allwise cutout API (documented at https://unwise.me/imgsearch/)."""
    import requests
    from astropy.io import fits
    from astropy.wcs import WCS

    r = requests.get(UNWISE_CUTOUT, params={"version": "allwise", "ra": f"{ra:.6f}", "dec": f"{dec:.6f}",
                                            "size": str(size_px), "bands": "1", "file_img_m": "on"},
                     timeout=timeout, headers={"User-Agent": "spherex-citizen-science-pool-builder"})
    r.raise_for_status()
    best = None
    with tarfile.open(fileobj=io.BytesIO(r.content), mode="r:gz") as tar:
        for m in tar.getmembers():
            if m.isfile() and m.name.endswith(".fits") and "-w1-" in m.name:
                with fits.open(io.BytesIO(tar.extractfile(m).read())) as hdul:
                    data = hdul[0].data.astype(np.float32)
                    wcs = WCS(hdul[0].header)
                # Several tiles can overlap: keep the one with the most valid pixels.
                score = int(np.isfinite(data).sum() - (data == 0).sum())
                if best is None or score > best[0]:
                    best = (score, data, wcs)
    if best is None:
        raise RuntimeError("unWISE answered without a W1 image")
    return best[1], best[2]


def spherex_stack(gp, center: tuple[float, float], fov_arcmin: float, a: argparse.Namespace) -> dict:
    """Latest SPHEREx visit covering `center`: broadband stack (mean of per-detector median
    stacks, display version: saturated cores visible) on a ~native grid, with its WCS."""
    from astropy.coordinates import SkyCoord

    from app.services.live import coordinate_target

    coord = SkyCoord(*center, unit="deg")
    rows = gp.dedupe(gp.query_sia(coord, gp.WIDE_COLLECTIONS, a.sia_budget_s), gp.WIDE_COLLECTIONS)
    visits = [v for v in gp.group_epochs(rows, 30.0) if len(v) >= 12]
    if not visits:
        return {"status": "rejected", "reason": "no_spherex_visit"}
    visit = gp.subsample_per_detector(visits[-1], a.per_detector)
    target = coordinate_target(*center)
    args = gp.default_args(fov=fov_arcmin, max_per_detector=a.per_detector, workers=a.workers,
                           retry_base_delay=5.0, download_timeout=a.timeout, no_cache=True, offline=False)
    ref_wcs, ref_shape = gp.build_reference_wcs(coord, fov_arcmin, a.png_size)
    exposures = gp.process_rows(visit, target, ref_wcs, ref_shape, args, "pool", None)
    if len(exposures) < 3:
        return {"status": "failed", "reason": f"only {len(exposures)} usable SPHEREx exposures"}
    epoch = gp.EpochProduct.build("SPHEREx", exposures)
    image = epoch.stack_of(sorted(epoch.display_stacks), display=True)[0]
    mjd = float(np.median([e.mjd for e in exposures]))
    return {"status": "ok", "image": image, "wcs": ref_wcs, "mjd": mjd, "n_exposures": len(exposures),
            "detectors": sorted(epoch.display_stacks), "obs_ids": sorted({e.obs_id for e in exposures})}


# --- SPHEREx DR1 HiPS cube on the public S3 bucket (no IRSA) --------------------------------- #
# Layout checked on 2026-11-15 (scripts/s3_range_test.py): every Norder5 tile is a 512x512x102
# float32 MJy/sr cube (HDU 1 "IMAGE", RA---HPX/DEC--HPX, ICRS), then NHITS, FLAGS and a
# WCS-WAVE table; the primary header has MJD-BEG/MJD-END of the inputs. Channels are ordered by
# detector, 17 each: D1 0.75-1.1 um (0-16), D2 1.1-1.6 (17-33), D3 1.6-2.4 (34-50), D4 2.4-3.8,
# D5 3.8-4.4, D6 4.4-5.0 um.
HIPS_ROOT = "https://nasa-irsa-spherex.s3.amazonaws.com/dr1/hips5_cube/hips_20261026/Norder5"
HIPS_ORDER = 5
# D2 + D3 (1.1-2.4 um), every other channel: nearby high proper-motion stars (M dwarfs, white
# dwarfs) are brightest there, while the thermal zodiacal background climbs beyond ~3.5 um.
# Every other channel halves the Range requests; the median resists single-channel artefacts.
HIPS_CHANNELS = tuple(range(17, 51, 2))


def http_range(url: str, start: int, end: int, timeout: float) -> bytes:
    import requests

    r = requests.get(url, headers={"Range": f"bytes={start}-{end}",
                                   "User-Agent": "spherex-citizen-science-pool-builder"}, timeout=timeout)
    r.raise_for_status()
    return r.content


def fits_header_at(url: str, pos: int, timeout: float):
    """(header, data offset) of the HDU whose header starts at byte `pos`, read by Range requests."""
    from astropy.io import fits

    end_card = b"END" + b" " * 77
    buf = b""
    while len(buf) < 2880 * 40:
        buf += http_range(url, pos + len(buf), pos + len(buf) + 2880 * 4 - 1, timeout)
        for k in range(0, len(buf) - 79, 80):
            if buf[k:k + 80] == end_card:
                size = (k // 2880 + 1) * 2880
                return fits.Header.fromstring(buf[:size].decode("ascii")), pos + size
    raise RuntimeError(f"no END card in the first 40 blocks of {url} at {pos}")


def hips_celestial_wcs(header):
    """The tile's 2-D HPX WCS (the third, tabulated wavelength axis dropped)."""
    from astropy.wcs import WCS

    h = header.copy()
    for k in list(h.keys()):
        if (k[-1:] == "3" and k[:-1] in ("NAXIS", "CTYPE", "CRPIX", "CDELT", "CRVAL", "CUNIT", "CNAME")) \
                or k.startswith(("PS3_", "PV3_")) or (k.startswith("PC") and "3" in k[2:]):
            del h[k]
    h["NAXIS"], h["WCSAXES"] = 2, 2
    return WCS(h)


def hips_tile(ra: float, dec: float) -> int:
    from astropy import units as u
    from astropy.coordinates import Latitude, Longitude
    from astropy_healpix import HEALPix

    return int(HEALPix(nside=2**HIPS_ORDER, order="nested").lonlat_to_healpix(
        Longitude(ra * u.deg), Latitude(dec * u.deg)))


def hips_stack(grid, shape: tuple[int, int], a: argparse.Namespace) -> dict:
    """Median of HIPS_CHANNELS over the round grid, read from the HiPS tiles by Range requests
    (only the rows the grid needs), reprojected HPX -> grid. Tiles meeting the grid are combined."""
    from concurrent.futures import ThreadPoolExecutor

    from reproject import reproject_interp

    ny, nx = shape
    edge = np.linspace(0, nx - 1, 13)
    px = np.concatenate([edge, edge, np.zeros(13), np.full(13, nx - 1.0), [nx / 2]])
    py = np.concatenate([np.zeros(13), np.full(13, ny - 1.0), edge, edge, [ny / 2]])
    sky = grid.pixel_to_world(px, py)
    tiles = sorted({hips_tile(float(r), float(d)) for r, d in zip(sky.ra.deg, sky.dec.deg)})
    total = np.zeros(shape)
    count = np.zeros(shape)
    beg, end, nbytes, pixel_arcsec = [], [], 0, None
    for npix in tiles:
        url = f"{HIPS_ROOT}/Dir{npix // 10000 * 10000}/Npix{npix}_cube.fits"
        h0, d0 = fits_header_at(url, 0, a.timeout)
        h1, d1 = fits_header_at(url, d0, a.timeout)
        if h1.get("EXTNAME") != "IMAGE" or h1.get("NAXIS3") != 102 or h1.get("BITPIX") != -32:
            raise RuntimeError(f"unexpected HiPS layout in {url}")
        w = hips_celestial_wcs(h1)
        pixel_arcsec = float(np.sqrt(abs(np.linalg.det(w.pixel_scale_matrix))) * 3600)
        tx, ty = w.world_to_pixel(sky)
        n1, n2 = h1["NAXIS1"], h1["NAXIS2"]
        x0, x1 = max(int(np.floor(np.nanmin(tx))) - 3, 0), min(int(np.ceil(np.nanmax(tx))) + 3, n1 - 1)
        y0, y1 = max(int(np.floor(np.nanmin(ty))) - 3, 0), min(int(np.ceil(np.nanmax(ty))) + 3, n2 - 1)
        if x0 > x1 or y0 > y1:
            continue
        rows = y1 - y0 + 1

        def plane(c: int) -> np.ndarray:
            start = d1 + 4 * (c * n1 * n2 + y0 * n1)
            raw = http_range(url, start, start + 4 * rows * n1 - 1, a.timeout)
            return np.frombuffer(raw, ">f4").reshape(rows, n1)[:, x0:x1 + 1].astype(np.float32)

        with ThreadPoolExecutor(max_workers=8) as pool:
            planes = list(pool.map(plane, HIPS_CHANNELS))
        nbytes += len(HIPS_CHANNELS) * 4 * rows * n1
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            img = np.nanmedian(np.stack(planes), axis=0)
        sub = w[y0:y1 + 1, x0:x1 + 1]
        out, foot = reproject_interp((img, sub), grid, shape_out=shape, order="bilinear")
        ok = (foot > 0) & np.isfinite(out)
        total[ok] += out[ok]
        count[ok] += 1
        beg.append(float(h0["MJD-BEG"]))
        end.append(float(h0["MJD-END"]))
    if not count.any():
        return {"status": "rejected", "reason": "no_hips_coverage"}
    image = np.where(count > 0, total / np.maximum(count, 1), np.nan)
    return {"status": "ok", "image": image, "mjd_beg": min(beg), "mjd_end": max(end), "tiles": tiles,
            "bytes": nbytes, "pixel_arcsec": pixel_arcsec, "coverage": float((count > 0).mean())}


def centroid(img: np.ndarray, x0: float, y0: float, radius: float, half: float) -> tuple[float, float] | None:
    """Light above the image median in a +-`half` box, started on the brightest pixels within
    `radius` of (x0, y0) and recentred until it converges (same measure as tests/test_game.py,
    which checks the game's PNGs; started on the image, not on the prediction)."""
    yy, xx = np.indices(img.shape)
    near = (np.hypot(xx - x0, yy - y0) <= radius) & np.isfinite(img)
    if not near.any():
        return None
    med = float(np.nanmedian(img))
    vals = np.where(near, img, -np.inf)
    top = near & (vals >= vals.max() - 0.1 * (vals.max() - med))
    cx, cy = float(xx[top].mean()), float(yy[top].mean())
    light = np.clip(np.nan_to_num(img - med), 0, None)
    for _ in range(10):
        w = np.where((np.abs(xx - cx) <= half) & (np.abs(yy - cy) <= half), light, 0.0)
        if w.sum() <= 0:
            return None
        nx, ny = float((w * xx).sum() / w.sum()), float((w * yy).sum() / w.sum())
        moved = math.hypot(nx - cx, ny - cy)
        cx, cy = nx, ny
        if moved < 0.01:
            break
    return cx, cy


def robust_sigma(img: np.ndarray) -> tuple[float, float]:
    v = img[np.isfinite(img)]
    med = float(np.median(v))
    return med, float(1.4826 * np.median(np.abs(v - med))) or 1e-9


def peak_near(img: np.ndarray, x: float, y: float, radius: float) -> float:
    yy, xx = np.indices(img.shape)
    near = (np.hypot(xx - x, yy - y) <= radius) & np.isfinite(img)
    return float(img[near].max()) if near.any() else float("nan")


def qa_image(img: np.ndarray, here: tuple[float, float], there: tuple[float, float], scale: float,
             tol_arcsec: float, min_snr: float, max_ghost: float, max_clutter: float | None = None) -> dict:
    """`here`: the star's predicted 0-based grid position in this image; `there`: its position at
    the other date, which must look empty. Positions in 0-based grid pixels (FITS orientation)."""
    med, sig = robust_sigma(img)
    r_search = 8.0 / scale
    peak = peak_near(img, *here, r_search) - med
    out = {"snr": round(peak / sig, 1)}
    c = centroid(img, *here, r_search, 7.0 / scale)
    out["offset_arcsec"] = None if c is None else round(math.hypot(c[0] - here[0], c[1] - here[1]) * scale, 1)
    out["centroid_px"] = None if c is None else [round(c[0], 2), round(c[1], 2)]  # 0-based grid, +y up
    ghost = peak_near(img, *there, 6.0 / scale) - med
    out["ghost_ratio"] = round(ghost / peak, 2) if peak > 0 else None
    # Clutter: nothing outside 25" of both positions may reach `max_clutter` of the star's peak,
    # or the player could take it for the mover (e.g. the streak artefacts of the HiPS tiles).
    yy, xx = np.indices(img.shape)
    away = np.isfinite(img) & (np.hypot(xx - here[0], yy - here[1]) > 25.0 / scale) \
        & (np.hypot(xx - there[0], yy - there[1]) > 25.0 / scale)
    other = float(img[away].max()) - med if away.any() else 0.0
    out["clutter_ratio"] = round(other / peak, 2) if peak > 0 else None
    out["visible"] = bool(peak / sig >= min_snr)
    out["on_target"] = out["offset_arcsec"] is not None and out["offset_arcsec"] <= tol_arcsec
    out["other_position_empty"] = out["ghost_ratio"] is not None and out["ghost_ratio"] <= max_ghost
    out["uncluttered"] = max_clutter is None or (out["clutter_ratio"] is not None
                                                 and out["clutter_ratio"] <= max_clutter)
    return out


def to_png(img: np.ndarray) -> bytes:
    """Grayscale asinh stretch (1st-99.7th percentile of the image), north up."""
    from PIL import Image

    v = img[np.isfinite(img)]
    lo, hi = (np.percentile(v, 1.0), np.percentile(v, 99.7)) if v.size else (0.0, 1.0)
    x = np.clip((np.nan_to_num(img, nan=lo) - lo) / max(hi - lo, 1e-12), 0, 1)
    u8 = (np.arcsinh(8 * x) / np.arcsinh(8) * 255).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(np.flipud(u8), mode="L").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# One round
# --------------------------------------------------------------------------- #


def build_round(gp, star: dict, a: argparse.Namespace, salt: str, static_dir: Path) -> dict:
    from astropy.time import Time
    from reproject import reproject_interp

    timings: dict[str, float] = {}
    t0 = time.monotonic()
    sid = star["source_id"]
    pm = math.hypot(star["pmra"], star["pmdec"]) / 1000.0

    # 1. Isolation (Gaia, cheap): nothing comparable within 15" of either date's position.
    pos_w = gaia_at(star, WISE_EPOCH_YR)
    pos_now = gaia_at(star, 2026.0)
    mid = ((pos_w[0] + pos_now[0]) / 2, (pos_w[1] + pos_now[1]) / 2)
    reach = sep_arcsec(pos_w, pos_now) / 2 + 20.0
    field = gaia_cone(*mid, reach)
    limit = star["phot_g_mean_mag"] + a.neighbour_dmag
    for other in field:
        if other["source_id"] == sid or other["phot_g_mean_mag"] > limit:
            continue
        d = min(sep_arcsec(gaia_at(other, WISE_EPOCH_YR), pos_w), sep_arcsec(gaia_at(other, 2026.0), pos_now))
        if d <= a.isolation_arcsec:
            return {"status": "rejected", "reason": "gaia_neighbour",
                    "detail": f"G={other['phot_g_mean_mag']:.1f} at {d:.0f}\""}
    timings["gaia_s"] = round(time.monotonic() - t0, 2)

    # 2. Round grid (needs the SPHEREx date, but the window only needs the 2026.0 position:
    #    the extra ~0.5 yr is < 1 SPHEREx pixel for every candidate but the fastest few).
    shift_now = sep_arcsec(pos_w, pos_now)
    fov = float(np.clip(shift_now + 2 * a.margin_arcsec, a.min_fov_arcsec, a.max_fov_arcsec))
    if shift_now + 2 * a.margin_arcsec > a.max_fov_arcsec:
        return {"status": "rejected", "reason": "shift_too_large_for_window", "detail": f"{shift_now:.0f}\""}
    rng = random.Random(f"{salt}|{sid}")
    slack = max(0.0, (fov - shift_now) / 2 - a.margin_arcsec)
    # offset of the grid centre from the path's midpoint, per axis, keeping both ends inside
    de, dn = (pos_now[0] - pos_w[0]) * math.cos(math.radians(mid[1])) * 3600, (pos_now[1] - pos_w[1]) * 3600
    max_e = max(0.0, fov / 2 - a.margin_arcsec - abs(de) / 2)
    max_n = max(0.0, fov / 2 - a.margin_arcsec - abs(dn) / 2)
    center = offset_position(*mid, rng.uniform(-max_e, max_e), rng.uniform(-max_n, max_n))
    scale = fov / a.png_size
    grid = grid_wcs(*center, a.png_size, scale)
    shape = (a.png_size, a.png_size)

    # 3. WISE (unWISE, ~2 s).
    t1 = time.monotonic()
    w_img, w_wcs = unwise_w1(*center, int(math.ceil(fov * math.sqrt(2) / UNWISE_PIXEL_ARCSEC)) + 10, a.timeout)
    wise = reproject_interp((w_img, w_wcs), grid, shape_out=shape, return_footprint=False, order="bilinear")
    del w_img
    timings["wise_s"] = round(time.monotonic() - t1, 2)

    def grid_xy(pos):  # 0-based, FITS orientation
        from astropy.coordinates import SkyCoord

        return tuple(float(v) for v in grid.world_to_pixel(SkyCoord(*pos, unit="deg")))

    wise_tol = a.tol_arcsec + 0.5 * pm  # +-0.5 yr on the WISE epoch
    qa_w = qa_image(wise, grid_xy(pos_w), grid_xy(pos_now), scale, wise_tol, a.min_snr, a.max_ghost)
    if not (qa_w["visible"] and qa_w["on_target"] and qa_w["other_position_empty"]):
        return {"status": "rejected", "reason": "wise_qa", "qa_wise": qa_w, "timings": timings}

    # 4. SPHEREx: Level 2 cutouts from IRSA (slow, SIA needed) or the DR1 HiPS cube on S3.
    t2 = time.monotonic()
    if a.spherex_source == "hips":
        sx = hips_stack(grid, shape, a)
        timings["spherex_s"] = round(time.monotonic() - t2, 2)
        if sx["status"] != "ok":
            return sx | {"timings": timings, "qa_wise": qa_w}
        yy, xx = np.indices(shape)
        near_both = np.zeros(shape, bool)
        for p in (pos_w, pos_now):
            gx, gy = grid_xy(p)
            near_both |= np.hypot(xx - gx, yy - gy) <= 20.0 / scale
        if sx["coverage"] < 0.9 or not np.isfinite(sx["image"][near_both]).all():
            return {"status": "rejected", "reason": "hips_partial_coverage", "detail": f"{sx['coverage']:.0%}",
                    "timings": timings, "qa_wise": qa_w}
        spherex = sx["image"]
        # The tile coadds every input between MJD-BEG and MJD-END: the answer uses the middle and
        # the tolerance absorbs half the span (pm x span / 2).
        s_time = Time((sx["mjd_beg"] + sx["mjd_end"]) / 2, format="mjd", scale="utc")
        half_span_yr = (sx["mjd_end"] - sx["mjd_beg"]) / 2 / 365.25
        s_tol = max(a.tol_arcsec, 0.5 * sx["pixel_arcsec"]) + pm * half_span_yr
    else:
        sx = spherex_stack(gp, center, fov / 60.0 + 0.5, a)  # grid + 15" margin per side
        timings["spherex_s"] = round(time.monotonic() - t2, 2)
        if sx["status"] != "ok":
            return sx | {"timings": timings, "qa_wise": qa_w}
        spherex = reproject_interp((sx["image"], sx["wcs"]), grid, shape_out=shape, return_footprint=False,
                                   order="bilinear")
        s_time = Time(sx["mjd"], format="mjd", scale="utc")
        s_tol = a.tol_arcsec
    pos_s = gaia_at(star, float(s_time.jyear))
    qa_s = qa_image(spherex, grid_xy(pos_s), grid_xy(pos_w), scale, s_tol, a.min_snr, a.max_ghost,
                    a.max_clutter)
    # The WISE image must also be empty where SPHEREx sees the star (exact date now).
    qa_w = qa_image(wise, grid_xy(pos_w), grid_xy(pos_s), scale, wise_tol, a.min_snr, a.max_ghost)
    if not (qa_s["visible"] and qa_s["on_target"] and qa_s["other_position_empty"] and qa_s["uncluttered"]):
        return {"status": "rejected", "reason": "spherex_qa", "qa_spherex": qa_s, "qa_wise": qa_w,
                "timings": timings}
    if not qa_w["other_position_empty"]:
        return {"status": "rejected", "reason": "wise_qa", "qa_spherex": qa_s, "qa_wise": qa_w,
                "timings": timings}

    shift = sep_arcsec(pos_w, pos_s)
    measured = math.hypot(qa_s["centroid_px"][0] - qa_w["centroid_px"][0],
                          qa_s["centroid_px"][1] - qa_w["centroid_px"][1]) * scale
    level = difficulty_of(shift / SPHEREX_PIXEL_ARCSEC)
    if level is None or shift / SPHEREX_PIXEL_ARCSEC < a.min_shift_px:
        return {"status": "rejected", "reason": "shift_below_minimum", "detail": f"{shift:.1f}\""}

    # 5. Files and manifest entry (the answer stays in the manifest, server side).
    names = {}
    for tag, img in (("a", spherex), ("b", wise)):
        name = hashlib.sha256(f"{salt}|{sid}|{tag}".encode()).hexdigest()[:24] + ".png"
        (static_dir / name).write_bytes(to_png(img))
        names[tag] = name
    pa, pb = to_png_xy(grid, a.png_size, pos_s), to_png_xy(grid, a.png_size, pos_w)
    def iso(t) -> str:
        return t.to_datetime(timezone=UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    s_date = iso(s_time)
    if a.spherex_source == "hips":
        spherex_meta = {"product": "SPHEREx DR1 HiPS cube (nasa-irsa-spherex S3, hips5_cube/hips_20261026)",
                        "tiles": sx["tiles"], "channels": list(HIPS_CHANNELS), "bandpass_um": [1.1, 2.4],
                        "date_range": [iso(Time(sx["mjd_beg"], format="mjd")), iso(Time(sx["mjd_end"], format="mjd"))],
                        "bytes_read": sx["bytes"]}
        spherex_px = round(sx["pixel_arcsec"], 2)
    else:
        spherex_meta = {"product": "SPHEREx Level 2 cutouts (IRSA)", "n_exposures": sx["n_exposures"],
                        "detectors": sx["detectors"], "obs_ids": sx["obs_ids"]}
        spherex_px = SPHEREX_PIXEL_ARCSEC
    timings["total_s"] = round(time.monotonic() - t0, 2)
    return {
        "status": "ok",
        "round": {
            "round_key": hashlib.sha256(f"{salt}|{sid}|round".encode()).hexdigest()[:12],
            "source": "wise_vs_spherex",
            "gaia_source_id": str(sid),
            "name": simbad_name(star) or f"Gaia DR3 {sid}",
            "gaia": {k: star[k] for k in ("ra", "dec", "pmra", "pmdec", "parallax", "phot_g_mean_mag")},
            "pm_arcsec_yr": round(pm, 3),
            "difficulty": level,
            "image_a": names["a"], "image_b": names["b"],
            "telescope_a": "SPHEREx", "telescope_b": "WISE",
            "date_a": s_date, "date_b": WISE_DATE,
            "date_a_range": spherex_meta.get("date_range", [s_date, s_date]),
            "date_b_range": ["2010-01-07T00:00:00Z", "2011-02-01T00:00:00Z"],
            "delta_t_days": round((s_time.jyear - WISE_EPOCH_YR) * 365.25, 1),
            "expected_shift_arcsec": round(shift, 1),
            "shift_spherex_px": round(shift / SPHEREX_PIXEL_ARCSEC, 2),
            "pos_a": [round(v, 2) for v in pa], "pos_b": [round(v, 2) for v in pb],
            "image_size": a.png_size,
            "pixel_scale_arcsec": round(scale, 4),  # of the PNGs (click tolerance)
            "spherex_pixel_scale_arcsec": spherex_px,  # resolution of the SPHEREx image
            "region": {"ra_deg": round(center[0], 6), "dec_deg": round(center[1], 6),
                       "fov_arcmin": round(fov / 60.0, 2)},
            "spherex": spherex_meta,
            "qa": {"spherex": qa_s, "wise": qa_w, "measured_shift_arcsec": round(measured, 1)},
        },
        "timings": timings,
    }


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #


def read_log(path: Path) -> dict[int, dict]:
    done: dict[int, dict] = {}
    if path.is_file():
        for line in path.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                done[int(r["source_id"])] = r  # the last attempt wins
    return done


def write_manifest(a: argparse.Namespace) -> Path:
    entries = [r["round"] for r in read_log(a.work / "build_log.jsonl").values() if r["status"] == "ok"]
    entries.sort(key=lambda r: r["round_key"])
    out = a.out_root / "app" / "data" / "game_pool.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "version": 1,
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "static_path": "/static/game_pool/",
        "answer_source": "Gaia DR3 position + proper motion propagated to each image date",
        "credits": ["SPHEREx (NASA/IPAC IRSA)", "unWISE / NASA/JPL-Caltech / D. Lang (Perimeter Institute)",
                    "Gaia DR3 (ESA)"],
        "rounds": entries}, indent=1))
    log.info("manifest: %d rounds -> %s", len(entries), out)
    return out


def cmd_build(a: argparse.Namespace) -> int:
    import generate_golden_path as gp
    from astropy.utils.data import conf

    gp.quiet_astropy()
    warnings.filterwarnings("ignore")
    conf.remote_timeout = a.timeout
    salt_file = a.work / "salt"  # kept so a resumed build names files the same way
    salt = os.getenv("GAME_POOL_SALT") or (salt_file.read_text().strip() if salt_file.is_file() else "")
    if not salt:
        salt = os.urandom(16).hex()
        salt_file.write_text(salt)
    static_dir = a.out_root / "app" / "static" / "game_pool"
    static_dir.mkdir(parents=True, exist_ok=True)
    stars = json.loads((a.work / "candidates.json").read_text())["stars"]
    if a.stars:
        wanted = {int(s) for s in a.stars}
        stars = [s for s in stars if s["source_id"] in wanted]
    log_path = a.work / "build_log.jsonl"
    done = read_log(log_path)
    n_new = 0
    for star in stars:
        if a.limit and n_new >= a.limit:
            break
        prev = done.get(star["source_id"])
        if prev and (prev["status"] in ("ok", "rejected") or not a.retry_failed):
            continue
        n_new += 1
        t0 = time.monotonic()
        try:
            res = build_round(gp, star, a, salt, static_dir)
        except Exception as exc:  # noqa: BLE001 - one star must not stop the batch
            res = {"status": "failed", "reason": type(exc).__name__, "detail": str(exc)[:300]}
        res["source_id"] = star["source_id"]
        res["G"] = star["phot_g_mean_mag"]
        res["pm"] = round(star["pm"] / 1000.0, 3)
        res["elapsed_s"] = round(time.monotonic() - t0, 2)
        res["at"] = datetime.now(UTC).isoformat(timespec="seconds")
        with log_path.open("a") as fh:
            fh.write(json.dumps(res, default=float) + "\n")
        extra = res.get("round", {}).get("difficulty") or res.get("reason")
        log.info("[%d] %s G=%.1f pm=%.2f\"/yr -> %s (%s) %.0f s", n_new, star["source_id"], res["G"],
                 res["pm"], res["status"], extra, res["elapsed_s"])
    write_manifest(a)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["candidates", "build", "manifest"])
    p.add_argument("--work", type=Path, default=WORK_DIR, help="candidates, build log and salt")
    p.add_argument("--out-root", type=Path, default=REPO_ROOT,
                   help="write app/static/game_pool/ and app/data/game_pool.json under this root")
    # candidates
    p.add_argument("--min-parallax", type=float, default=50.0, help="mas (indexed on the ARI mirror)")
    p.add_argument("--min-pm", type=float, default=1200.0, help="mas/yr; 1.2\"/yr x 15.3 yr = 3 SPHEREx px")
    p.add_argument("--min-abs-b", type=float, default=15.0)
    p.add_argument("--seed", type=int, default=1930)
    # build
    p.add_argument("--stars", nargs="+", help="only these Gaia DR3 source_ids")
    p.add_argument("--limit", type=int, default=0, help="at most N new stars this run")
    p.add_argument("--retry-failed", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--png-size", type=int, default=300)
    p.add_argument("--min-fov-arcsec", type=float, default=180.0)
    p.add_argument("--max-fov-arcsec", type=float, default=360.0)
    p.add_argument("--margin-arcsec", type=float, default=40.0, help="both positions this far from the edges")
    p.add_argument("--spherex-source", choices=["irsa", "hips"], default="irsa",
                   help="irsa: Level 2 cutouts (SIA + cutout service); hips: DR1 HiPS cube on S3")
    p.add_argument("--min-shift-px", type=float, default=3.0,
                   help="minimum expected shift in native SPHEREx px (use 5 with --spherex-source hips)")
    p.add_argument("--per-detector", type=int, default=2, help="SPHEREx exposures per detector (irsa)")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--timeout", type=float, default=60.0, help="per HTTP request (s)")
    p.add_argument("--sia-budget-s", type=float, default=60.0)
    p.add_argument("--isolation-arcsec", type=float, default=15.0)
    p.add_argument("--neighbour-dmag", type=float, default=2.5)
    p.add_argument("--tol-arcsec", type=float, default=4.0, help="centroid vs Gaia prediction")
    p.add_argument("--min-snr", type=float, default=5.0)
    p.add_argument("--max-ghost", type=float, default=0.25,
                   help="max (peak at the other date's position) / (star peak)")
    p.add_argument("--max-clutter", type=float, default=0.5,
                   help="SPHEREx: max (brightest other feature, >25\" from both positions) / (star peak)")
    p.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args()
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    for noisy in ("golden_path", "urllib3"):  # never "astropy": astropy installs its own logger class
        logging.getLogger(noisy).setLevel(logging.WARNING)
    a.work.mkdir(parents=True, exist_ok=True)
    if a.command == "candidates":
        return cmd_candidates(a)
    if a.command == "manifest":
        write_manifest(a)
        return 0
    return cmd_build(a)


if __name__ == "__main__":
    sys.exit(main())
