"""Pixel <-> sky geometry shared by the pipeline, the API and the tests.

Pure Python on purpose: the API and its tests must not need astropy.

Conventions (what the frontend relies on):

* A patch is a north-up, square-pixel gnomonic (TAN) image of
  `width_px x height_px` covering `fov_arcmin` on each side. East is left,
  so `cd1_1 < 0` and `cd2_2 > 0`.
* `wcs` uses the FITS convention: 1-based pixels, +Y up, pixel centres at
  integer values. `crpix = ((W + 1) / 2, (H + 1) / 2)` is the patch centre.
* `pixel_x` / `pixel_y` in the JSON are 0-based PNG indices, origin at the top-left.
  To convert between the two: `x_fits = pixel_x + 1`, `y_fits = H - pixel_y`.
* The pipeline stacks on a coarser `n x n` grid with the same footprint and
  centre. The PNG is an exact integer upscale of that grid when possible
  (see `stack_grid_size`).
"""

from __future__ import annotations

import math
from collections.abc import Sequence

SPHEREX_PIXEL_ARCSEC = 6.15


def patch_wcs(
    center_ra_deg: float,
    center_dec_deg: float,
    fov_arcmin: float,
    width_px: int,
    height_px: int,
) -> dict[str, float | str]:
    """FITS WCS (CD-matrix form) of a north-up patch centred on the given position."""
    return {
        "ctype1": "RA---TAN",
        "ctype2": "DEC--TAN",
        "crval1": center_ra_deg % 360.0,
        "crval2": center_dec_deg,
        "crpix1": (width_px + 1) / 2,
        "crpix2": (height_px + 1) / 2,
        "cd1_1": -(fov_arcmin / 60.0) / width_px,
        "cd1_2": 0.0,
        "cd2_1": 0.0,
        "cd2_2": (fov_arcmin / 60.0) / height_px,
    }


def stack_grid_size(
    fov_arcmin: float, png_size: int, native_arcsec: float = SPHEREX_PIXEL_ARCSEC
) -> int:
    """Edge (px) of the stacking grid: about native sampling, rounded up to a
    divisor of `png_size` (within 25%) so the PNG is an exact integer upscale.
    5' / 600 px gives 50 px of 6.0", so each grid pixel becomes 12x12 PNG px."""
    n = max(1, math.ceil(fov_arcmin * 60.0 / native_arcsec))
    for candidate in range(n, math.floor(n * 1.25) + 1):
        if png_size % candidate == 0:
            return candidate
    return n


def grid_to_png_float(x: float, y: float, n_grid: int, png_size: int) -> tuple[float, float]:
    """0-based grid pixel (FITS orientation, +Y up) -> continuous PNG coordinates
    (origin at the top-left corner of the image, +Y down)."""
    scale = png_size / n_grid
    return (x + 0.5) * scale, (n_grid - 0.5 - y) * scale


def grid_to_png(x: float, y: float, n_grid: int, png_size: int) -> tuple[int, int]:
    """0-based index of the PNG pixel that contains a grid position."""
    u, v = grid_to_png_float(x, y, n_grid, png_size)
    return (
        int(min(png_size - 1, max(0, math.floor(u)))),
        int(min(png_size - 1, max(0, math.floor(v)))),
    )


def png_to_world(wcs: dict, height_px: int, pixel_x: float, pixel_y: float) -> tuple[float, float]:
    """Centre of 0-based PNG pixel (pixel_x, pixel_y) -> (ra_deg, dec_deg)."""
    dx = (pixel_x + 1) - wcs["crpix1"]
    dy = (height_px - pixel_y) - wcs["crpix2"]
    xi = math.radians(wcs["cd1_1"] * dx + wcs["cd1_2"] * dy)
    eta = math.radians(wcs["cd2_1"] * dx + wcs["cd2_2"] * dy)
    ra0, dec0 = math.radians(wcs["crval1"]), math.radians(wcs["crval2"])
    denom = math.cos(dec0) - eta * math.sin(dec0)
    ra = ra0 + math.atan2(xi, denom)
    dec = math.atan2(math.sin(dec0) + eta * math.cos(dec0), math.hypot(xi, denom))
    return math.degrees(ra) % 360.0, math.degrees(dec)


def angular_separation_arcsec(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """Great-circle distance (haversine), in arcsec."""
    p1, p2 = math.radians(dec1), math.radians(dec2)
    dp, dl = p2 - p1, math.radians(ra2 - ra1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return math.degrees(2 * math.asin(min(1.0, math.sqrt(h)))) * 3600.0


def suppress_duplicates(
    detections: Sequence[tuple[float, float, float]], radius_px: float
) -> list[int]:
    """Greedy non-maximum suppression on (score, x, y) tuples.

    Keeps the highest score among detections within `radius_px` (inclusive)
    of each other, so split contours of one source are reported once. Returns
    the kept indices, best score first.
    """
    order = sorted(range(len(detections)), key=lambda i: detections[i][0], reverse=True)
    kept: list[int] = []
    r2 = radius_px * radius_px
    for i in order:
        _, x, y = detections[i]
        if all((x - detections[k][1]) ** 2 + (y - detections[k][2]) ** 2 > r2 for k in kept):
            kept.append(i)
    return kept
