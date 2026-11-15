"""Proper-motion propagation and sky -> image projection for catalogue objects.

Pure Python (no astropy), like app/core/patch_geometry.py.

Pixel convention of the output (the same one as the game, tests/test_game.py): continuous
coordinates on the PNG the user sees, origin at the TOP-LEFT corner, +x right, +y down,
pixel i spans [i, i+1). On the sky, North is up and East is LEFT (a normal sky chart): this
comes from the patch WCS (cd1_1 < 0), not from anything done here. A FITS image starts at the
bottom-left with 1-based pixel centres; the conversion happens inside `world_to_png`
(x = x_fits - 0.5, y = H - y_fits + 0.5), so callers never flip the axis themselves.

The WCS used is the one of the PNG that is served (`patch.wcs` of the sky-patch response:
a north-up TAN grid on the 600 x 600 PNG, already resampled and cropped by the pipeline),
not the WCS of the original SPHEREx FITS cutouts.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

from app.services.game import julian_year, world_to_png

SIMBAD_EPOCH_YR = 2000.0  # SIMBAD `ra`/`dec` are ICRS, epoch J2000 (CT.epoch=J2000 in its VOSI metadata)
MAS_TO_RAD = math.pi / 180.0 / 3.6e6


def propagate(ra_deg: float, dec_deg: float, pmra_mas_yr: float, pmdec_mas_yr: float,
              years: float) -> tuple[float, float]:
    """Move a position along its proper motion for `years` (ICRS degrees in and out).

    `pmra_mas_yr` is mu_alpha* = d(alpha)/dt * cos(delta), which is how SIMBAD's `pmra` is defined,
    so no cos(dec) is divided out here. The motion is applied on the tangent plane of the unit
    vector (linear in space), which stays correct at the poles where dividing by cos(dec) blows up.
    Parallax and radial velocity are ignored (below one pixel over a couple of decades).
    """
    a, d = math.radians(ra_deg), math.radians(dec_deg)
    p = (math.cos(d) * math.cos(a), math.cos(d) * math.sin(a), math.sin(d))
    east = (-math.sin(a), math.cos(a), 0.0)
    north = (-math.sin(d) * math.cos(a), -math.sin(d) * math.sin(a), math.cos(d))
    k = years * MAS_TO_RAD
    v = [p[i] + k * (pmra_mas_yr * east[i] + pmdec_mas_yr * north[i]) for i in range(3)]
    norm = math.sqrt(sum(c * c for c in v))
    x, y, z = (c / norm for c in v)
    return math.degrees(math.atan2(y, x)) % 360.0, math.degrees(math.asin(max(-1.0, min(1.0, z))))


def years_since_j2000(when: datetime) -> float:
    return julian_year(when.astimezone(UTC) if when.tzinfo else when.replace(tzinfo=UTC)) - SIMBAD_EPOCH_YR


def project(wcs: dict, width_px: int, height_px: int, ra_deg: float, dec_deg: float) -> tuple[float, float] | None:
    """(ra, dec) -> continuous PNG (x, y), or None when the pixel is outside the image
    (or the point is on the far side of the tangent plane)."""
    a0, d0 = math.radians(wcs["crval1"]), math.radians(wcs["crval2"])
    a, d = math.radians(ra_deg), math.radians(dec_deg)
    cosc = math.sin(d0) * math.sin(d) + math.cos(d0) * math.cos(d) * math.cos(a - a0)
    if cosc <= 1e-9:
        return None
    x, y = world_to_png(wcs, height_px, ra_deg, dec_deg)
    if not (0.0 <= x < width_px and 0.0 <= y < height_px):
        return None
    return x, y


def half_diagonal_deg(wcs: dict, width_px: int, height_px: int) -> float:
    """Radius of the circle that contains the whole image (half of its diagonal), in degrees."""
    w = width_px * math.hypot(wcs["cd1_1"], wcs["cd2_1"])
    h = height_px * math.hypot(wcs["cd1_2"], wcs["cd2_2"])
    return math.hypot(w, h) / 2.0
