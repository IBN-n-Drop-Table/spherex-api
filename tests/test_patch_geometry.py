import math

import pytest

from app.core.patch_geometry import (
    angular_separation_arcsec,
    grid_to_png,
    grid_to_png_float,
    patch_wcs,
    png_to_world,
    stack_grid_size,
    suppress_duplicates,
)

WISE_CENTER = (133.760005, -7.242258)


def test_patch_wcs_for_5_arcmin_600px():
    wcs = patch_wcs(*WISE_CENTER, fov_arcmin=5.0, width_px=600, height_px=600)
    assert wcs["ctype1"] == "RA---TAN" and wcs["ctype2"] == "DEC--TAN"
    assert (wcs["crpix1"], wcs["crpix2"]) == (300.5, 300.5)
    assert wcs["cd1_1"] == pytest.approx(-0.000138889, rel=1e-5)
    assert wcs["cd2_2"] == pytest.approx(0.000138889, rel=1e-5)
    assert wcs["cd1_2"] == wcs["cd2_1"] == 0.0


def test_stack_grid_is_integer_downscale_of_png():
    assert stack_grid_size(5.0, 600) == 50  # 6.0"/px, 12x12 PNG px each
    assert 600 % stack_grid_size(5.0, 600) == 0
    assert stack_grid_size(5.0, 601) == 49  # no divisor nearby -> native sampling


def test_png_centre_and_orientation():
    wcs = patch_wcs(*WISE_CENTER, 5.0, 600, 600)
    # FITS pixel (300.5, 300.5) is the corner shared by 0-based PNG pixels 299/300.
    ra, dec = png_to_world(wcs, 600, 299.5, 299.5)
    assert angular_separation_arcsec(ra, dec, *WISE_CENTER) < 1e-6

    ra_left, _ = png_to_world(wcs, 600, 0, 300)
    ra_right, _ = png_to_world(wcs, 600, 599, 300)
    assert ra_left > ra_right  # East is left
    _, dec_top = png_to_world(wcs, 600, 300, 0)
    _, dec_bottom = png_to_world(wcs, 600, 300, 599)
    assert dec_top > dec_bottom  # North is up


def test_patch_spans_fov():
    wcs = patch_wcs(*WISE_CENTER, 5.0, 600, 600)
    # Outer edges of the first and last PNG pixels: 5 arcmin apart.
    left = png_to_world(wcs, 600, -0.5, 299.5)
    right = png_to_world(wcs, 600, 599.5, 299.5)
    assert angular_separation_arcsec(*left, *right) / 60 == pytest.approx(5.0, rel=1e-4)


def test_grid_to_png_matches_patch_wcs():
    """A grid pixel and the PNG pixel it maps to are the same sky position."""
    n, size = 50, 600
    wcs = patch_wcs(*WISE_CENTER, 5.0, size, size)
    grid_scale = 5.0 / 60 / n
    for x, y in [(0, 0), (49, 49), (24.5, 24.5), (10.3, 37.8)]:
        u, v = grid_to_png_float(x, y, n, size)
        ra_png, dec_png = png_to_world(wcs, size, u - 0.5, v - 0.5)
        # Same point straight from the grid's own TAN WCS (crpix (n+1)/2, 1-based).
        grid_wcs = {**wcs, "crpix1": (n + 1) / 2, "crpix2": (n + 1) / 2,
                    "cd1_1": -grid_scale, "cd2_2": grid_scale}
        ra_grid, dec_grid = png_to_world(grid_wcs, n, x, n - 1 - y)
        assert angular_separation_arcsec(ra_png, dec_png, ra_grid, dec_grid) < 1e-6


def test_grid_to_png_indices_are_0_based_top_left():
    # Grid pixel (0, 49) is the top-left 12x12 block; its centre is PNG pixel (6, 6).
    assert grid_to_png(0, 49, 50, 600) == (6, 6)
    assert grid_to_png(-0.5, 49.5, 50, 600) == (0, 0)  # outer top-left corner
    assert grid_to_png(49, 0, 50, 600) == (594, 594)
    assert grid_to_png(49.49, -0.49, 50, 600) == (599, 599)
    assert grid_to_png(-5, 99, 50, 600) == (0, 0)  # clamped


def test_suppress_duplicates_merges_split_contours():
    # WISE 0855 old output: anom_02 (588,106) snr 18.69 and anom_05 (584,100) snr 14.38.
    detections = [(14.38, 584, 100), (18.69, 588, 106), (45.49, 528, 166), (7.0, 540, 166)]
    kept = suppress_duplicates(detections, radius_px=12)
    assert kept == [2, 1]  # best first; anom_05 and the 12 px neighbour of 45.49 dropped
    assert suppress_duplicates(detections, radius_px=5) == [2, 1, 0, 3]
    assert suppress_duplicates([], 12) == []


def test_suppression_keeps_highest_snr_not_first_seen():
    kept = suppress_duplicates([(5.0, 0, 0), (9.0, 3, 4), (6.0, 6, 8)], radius_px=6)
    assert kept == [1]  # (3,4) is within 5 px of both others


def test_angular_separation():
    assert angular_separation_arcsec(10, 0, 10, 1) == pytest.approx(3600)
    assert angular_separation_arcsec(359.9999, 0, 0.0001, 0) == pytest.approx(0.72, rel=1e-3)
    assert math.isclose(angular_separation_arcsec(1, 2, 1, 2), 0)
