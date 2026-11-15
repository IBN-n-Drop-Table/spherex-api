"""Proper motion + projection onto the served PNG, against a synthetic WCS with known answers."""

import math
from datetime import UTC, datetime

import pytest

from app.core.patch_geometry import angular_separation_arcsec, patch_wcs
from app.core.sky_projection import half_diagonal_deg, project, propagate, years_since_j2000
from app.core.simbad_otypes import IR_GALAXY_OTYPES, COMMON_STAR_OTYPES, adql_condition, matches

W = H = 600
ARCMIN = 1 / 60.0  # deg


def wcs_at(ra, dec, fov=5.0):
    return patch_wcs(ra, dec, fov, W, H)  # 0.5 arcsec per pixel, north up, east left


def test_centre_lands_in_the_middle_of_the_image():
    assert project(wcs_at(180.0, 0.0), W, H, 180.0, 0.0) == pytest.approx((300.0, 300.0), abs=1e-6)


def test_north_is_up_and_east_is_left_with_the_top_left_origin():
    w = wcs_at(180.0, 0.0)
    x, y = project(w, W, H, 180.0, 1 * ARCMIN)           # 1' north = 120 px
    assert (x, y) == pytest.approx((300.0, 180.0), abs=0.05)  # smaller y = higher up the image
    x, y = project(w, W, H, 180.0 + 1 * ARCMIN, 0.0)      # 1' east
    assert (x, y) == pytest.approx((180.0, 300.0), abs=0.05)  # smaller x = further left
    x, y = project(w, W, H, 180.0, -1 * ARCMIN)           # south goes down
    assert y == pytest.approx(420.0, abs=0.05)


def test_fits_origin_is_not_the_png_origin():
    """The pixel at the bottom-left corner of the FITS grid is the bottom-left of the PNG, y = H - 0.5."""
    w = wcs_at(10.0, 0.0)
    ra, dec = 10.0 + 2.4999 * ARCMIN, -2.4999 * ARCMIN   # just inside the SE corner: east=left, south=down
    x, y = project(w, W, H, ra, dec)
    assert x < 1 and y > H - 1


def test_outside_the_image_is_dropped():
    w = wcs_at(180.0, 0.0)
    assert project(w, W, H, 180.0, 2.6 * ARCMIN) is None
    assert project(w, W, H, 180.0 + 2.6 * ARCMIN, 0.0) is None
    assert project(w, W, H, 0.0, 0.0) is None  # the far side of the sphere


def test_ra_wraps_around_zero():
    w = wcs_at(0.0, 10.0)
    cos = math.cos(math.radians(10.0))
    east = project(w, W, H, (1 * ARCMIN / cos) % 360.0, 10.0)        # 1' east of the centre, RA ~ 0.017
    west = project(w, W, H, (-1 * ARCMIN / cos) % 360.0, 10.0)       # RA ~ 359.983
    assert east == pytest.approx((180.0, 300.0), abs=0.2)
    assert west == pytest.approx((420.0, 300.0), abs=0.2)


def test_near_the_north_pole():
    w = wcs_at(30.0, 89.97)
    assert project(w, W, H, 30.0, 89.97) == pytest.approx((300.0, 300.0), abs=1e-6)
    x, y = project(w, W, H, 30.0, 89.97 + 1 * ARCMIN)  # 1' further north on the same meridian
    assert math.dist((x, y), (300, 300)) == pytest.approx(120.0, abs=0.5)
    assert y < 300  # "north" along the meridian of the centre is up


def test_propagation_moves_by_pm_times_years_and_works_at_the_pole():
    ra, dec = propagate(100.0, 0.0, 0.0, 1000.0, 10.0)               # 1"/yr north, 10 yr
    assert (ra, dec) == pytest.approx((100.0, 10 / 3600), abs=1e-9)
    ra, dec = propagate(100.0, 0.0, 1000.0, 0.0, 10.0)               # east
    assert (ra, dec) == pytest.approx((100.0 + 10 / 3600, 0.0), abs=1e-9)
    # at dec 89.9999 the plain `pm/cos(dec)` formula would explode; the vector one moves 10" for 10 yr
    ra2, dec2 = propagate(0.0, 89.9999, 1000.0, 0.0, 10.0)
    assert angular_separation_arcsec(0.0, 89.9999, ra2, dec2) == pytest.approx(10.0, rel=1e-3)
    assert 0.0 <= ra2 < 360.0


def test_propagation_with_zero_years_is_identity_and_ra_stays_in_range():
    assert propagate(359.9999, 5.0, -800.0, 100.0, 0.0) == pytest.approx((359.9999, 5.0), abs=1e-9)
    ra, _ = propagate(359.9999, 5.0, 3000.0, 0.0, 25.0)
    assert 0.0 <= ra < 1.0


def test_years_since_j2000():
    assert years_since_j2000(datetime(2000, 1, 1, 12, tzinfo=UTC)) == pytest.approx(0.0)
    assert years_since_j2000(datetime(2025, 1, 1, 12, tzinfo=UTC)) == pytest.approx(25.0, abs=0.01)
    assert years_since_j2000(datetime(2025, 1, 1, 12)) == years_since_j2000(datetime(2025, 1, 1, 12, tzinfo=UTC))


def test_half_diagonal_is_half_of_the_diagonal_of_the_patch():
    assert half_diagonal_deg(wcs_at(10, 10, 5.0), W, H) == pytest.approx(5 * math.sqrt(2) / 2 / 60, rel=1e-9)


def test_type_groups():
    assert adql_condition("all") is None
    assert adql_condition("no_common_stars").startswith("b.otype NOT IN ('*', '**'")
    assert adql_condition("ir_galaxies").startswith("b.otype IN ('G'")
    assert not set(COMMON_STAR_OTYPES) & set(IR_GALAXY_OTYPES)
    assert not matches("no_common_stars", "*") and matches("no_common_stars", "BD*")
    assert matches("ir_galaxies", "QSO") and not matches("ir_galaxies", "BD*")
    assert matches("all", "anything")
