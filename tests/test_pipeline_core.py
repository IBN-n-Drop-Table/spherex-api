"""Unit tests of the science core in scripts/generate_golden_path.py.

Photometry, stacking, the 102-channel spectrum, light curves, saturation,
chemical windows, proper motion, cutout cache keys, visit grouping and the
cache-only mode. They need the science stack (requirements-science.txt) and
are skipped without it.
"""

import importlib.util
import math
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

for _mod in ("numpy", "cv2", "astropy", "reproject", "matplotlib", "PIL", "pyvo", "requests"):
    pytest.importorskip(_mod)

import numpy as np  # noqa: E402
from astropy.io import fits  # noqa: E402
from astropy.table import Table  # noqa: E402
from astropy.time import Time  # noqa: E402

from app.core.targets import CATALOG  # noqa: E402

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "generate_golden_path.py"
N = 50


@pytest.fixture(scope="module")
def gp():
    name = "spherex_golden_path_pipeline_core_tests"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    module.quiet_astropy()
    return module


def exposure(gp, det=1, mjd=60800.0, wl=1.0, image=None, ap_flux=1.0, ap_var=0.01, saturated=False):
    shape = (N, N)
    img = np.zeros(shape) if image is None else image
    return gp.Exposure(obs_id=f"o{det}_{mjd}", detector=det, mjd=mjd, wavelength_um=wl, bandwidth_um=0.02,
                       image=img, display=img.copy(), variance=np.ones(shape), flags=np.zeros(shape),
                       wave_map=np.full(shape, wl, np.float32), bw_map=np.full(shape, 0.02, np.float32),
                       flag_names={}, ap_flux=ap_flux, ap_var=ap_var, saturated=saturated)


# --------------------------------------------------------------------------- #
# Photometry
# --------------------------------------------------------------------------- #
def test_aperture_photometry_subtracts_the_local_background(gp):
    img = np.full((N, N), 3.0)  # sky pedestal
    yy, xx = np.indices((N, N))
    img[np.hypot(xx - 25, yy - 25) <= 1.5] += 10.0  # flat source filling the aperture
    flux, var = gp.aperture_photometry(img, np.full((N, N), 0.04), 25, 25, 1.5, 4.0, 7.0)
    n = int((np.hypot(xx - 25, yy - 25) <= 1.5).sum())
    assert flux == pytest.approx(10.0)  # mean surface brightness above the sky
    assert var == pytest.approx(0.04 / n)  # variance of a mean


def test_aperture_photometry_refuses_a_mostly_masked_aperture(gp):
    img = np.ones((N, N))
    yy, xx = np.indices((N, N))
    img[np.hypot(xx - 25, yy - 25) <= 1.5] = np.nan  # e.g. a saturated core
    assert gp.aperture_photometry(img, np.ones((N, N)), 25, 25, 1.5, 4.0, 7.0) == (None, None)


# --------------------------------------------------------------------------- #
# Stacking
# --------------------------------------------------------------------------- #
def test_median_stack_rejects_an_outlier_frame_and_ignores_nans(gp):
    rng = np.random.default_rng(1)
    frames = [rng.normal(0, 0.1, (N, N)) for _ in range(5)]
    frames[2][10, 10] = 1e4  # cosmic ray in one frame
    frames[3][20, 20] = np.nan  # masked pixel in another
    med, var = gp.median_stack(frames, [np.ones((N, N))] * 5)
    assert abs(med[10, 10]) < 1.0
    assert np.isfinite(med[20, 20])
    assert var[0, 0] == pytest.approx((math.pi / 2) / 5)  # variance of a median of 5
    assert var[20, 20] == pytest.approx((math.pi / 2) / 4)  # one frame fewer there


def test_median_stack_marks_pixels_without_data(gp):
    frames = [np.ones((N, N)), np.ones((N, N))]
    for f in frames:
        f[0, 0] = np.nan
    med, var = gp.median_stack(frames, [np.ones((N, N))] * 2)
    assert np.isnan(med[0, 0]) and np.isnan(var[0, 0])


def test_mean_of_detector_stacks_weights_detectors_equally(gp):
    a = (np.full((N, N), 2.0), np.full((N, N), 1.0))
    b = (np.full((N, N), 4.0), np.full((N, N), 1.0))
    img, var = gp.mean_of([a, b])
    assert img[5, 5] == pytest.approx(3.0) and var[5, 5] == pytest.approx(0.5)


# --------------------------------------------------------------------------- #
# Spectrum and light curve
# --------------------------------------------------------------------------- #
def test_binned_spectrum_maps_to_102_lvf_channels(gp):
    samples = []
    for det, (lo, hi) in gp.LVF_BANDS.items():
        edges = np.geomspace(lo, hi, gp.CHANNELS_PER_DETECTOR + 1)
        centres = np.sqrt(edges[:-1] * edges[1:])
        samples += [gp.Sample(det, float(c), 0.02, 1.0, 0.01) for c in centres]
    spec = gp.binned_spectrum(samples)
    assert len(spec) == 6 * 17 == 102
    assert {k[0] for k in spec} == set(range(1, 7))


def test_binned_spectrum_is_inverse_variance_weighted_and_drops_bad_samples(gp):
    s = [gp.Sample(2, 1.30, 0.02, 1.0, 1.0), gp.Sample(2, 1.30, 0.02, 3.0, 0.25),
         gp.Sample(2, 1.30, 0.02, 99.0, 0.0),  # zero variance: dropped
         gp.Sample(2, 1.30, 0.02, float("nan"), 0.1),  # NaN flux: dropped
         gp.Sample(9, 1.30, 0.02, 5.0, 0.1)]  # unknown detector: dropped
    spec = gp.binned_spectrum(s)
    assert len(spec) == 1
    band = next(iter(spec.values()))
    assert band["flux_mjy_sr"] == pytest.approx((1.0 * 1 + 3.0 * 4) / 5)
    assert band["variance"] == pytest.approx(1 / 5)


def test_light_curve_never_invents_a_zero(gp):
    when = datetime(2025, 5, 1, tzinfo=UTC)
    sat = gp.EpochProduct(epoch_id="E1", when=when, exposures=[exposure(gp, ap_flux=None, saturated=True)])
    point = gp.light_curve_point(sat, {}, set())
    assert point["flux_mjy_sr"] is None and "satura" in point["note"]
    empty = gp.EpochProduct(epoch_id="E2", when=when, exposures=[exposure(gp, ap_flux=None)])
    point = gp.light_curve_point(empty, {}, set())
    assert point["flux_mjy_sr"] is None and point["note"]


def test_light_curve_uses_only_channels_every_visit_shares(gp):
    ep = gp.EpochProduct(epoch_id="E1", when=datetime(2025, 5, 1, tzinfo=UTC), exposures=[exposure(gp)])
    spec = {(1, 0): {"flux_mjy_sr": 2.0, "variance": 0.1}, (1, 1): {"flux_mjy_sr": 100.0, "variance": 0.1}}
    point = gp.light_curve_point(ep, spec, {(1, 0)})
    assert point["flux_mjy_sr"] == pytest.approx(2.0) and point["note"] is None
    point = gp.light_curve_point(ep, spec, {(5, 3)})  # nothing in common: own channels, flagged
    assert point["flux_mjy_sr"] == pytest.approx(51.0) and "aproximada" in point["note"]


# --------------------------------------------------------------------------- #
# Saturation and chemical filters
# --------------------------------------------------------------------------- #
def test_epoch_is_saturated_only_when_most_covering_frames_are(gp):
    when = datetime(2025, 5, 1, tzinfo=UTC)
    two_of_three = [exposure(gp, saturated=True), exposure(gp, saturated=True), exposure(gp)]
    assert gp.EpochProduct.build("E", two_of_three).saturated is True
    one_of_three = [exposure(gp, saturated=True), exposure(gp), exposure(gp)]
    assert gp.EpochProduct.build("E", one_of_three).saturated is False
    assert gp.EpochProduct(epoch_id="E", when=when, exposures=[]).saturated is False


@pytest.mark.parametrize("wl, molecule", [(1.5, "H2O"), (2.7, "H2O"), (4.3, "CO2"), (4.67, "CO")])
def test_chemical_filters_use_frames_inside_their_window(gp, wl, molecule):
    star_in, star_out = np.zeros((N, N)), np.zeros((N, N))
    star_in[10, 10] = 100.0
    star_out[40, 40] = 100.0
    inside = exposure(gp, det=6 if wl > 4.4 else 5 if wl > 3.8 else 3, wl=wl, image=star_in)
    outside = exposure(gp, det=1, wl=0.9, image=star_out)
    ep = gp.EpochProduct.build("E", [inside, outside])
    stack = ep.chemical_stack(molecule, display=False)
    assert stack is not None
    assert stack[10, 10] == pytest.approx(100.0)  # the in-window frame, alone (not a median of 2)
    assert stack[40, 40] == pytest.approx(0.0)  # the out-of-window frame never enters
    others = set(gp.CHEMICAL_WINDOWS_UM) - {molecule}
    assert all(ep.chemical_stack(m) is None for m in others if not any(
        lo <= wl <= hi for lo, hi in gp.CHEMICAL_WINDOWS_UM[m]))


def test_flag_bits_decode_from_the_header(gp):
    hdr = fits.Header()
    hdr["MSKN0000"] = "transient"
    hdr["MSKN0021"] = "SOURCE"
    names = gp.decode_flag_names(hdr)
    assert names == {0: "TRANSIENT", 21: "SOURCE"}
    assert gp.flag_names_at((1 << 21) | 1, names) == ["TRANSIENT", "SOURCE"]
    assert gp.decode_flag_names(fits.Header()) == gp.DEFAULT_FLAG_BITS  # fallback table


# --------------------------------------------------------------------------- #
# Astrometry and cutouts
# --------------------------------------------------------------------------- #
def test_proper_motion_moves_barnards_star_north(gp):
    t = CATALOG["BARNARDS_STAR"]
    a = gp.position_at(t, Time("2025-01-01T00:00:00"))
    b = gp.position_at(t, Time("2026-01-01T00:00:00"))
    assert a.separation(b).arcsec == pytest.approx(10.39, rel=0.01)
    assert b.dec.deg > a.dec.deg
    ref = gp.position_at(t, Time(2016.0, format="jyear"))
    assert ref.ra.deg == pytest.approx(t.ref_ra_deg, abs=1e-9) and ref.dec.deg == pytest.approx(t.ref_dec_deg)


def test_cutout_url_is_a_stable_cache_key(gp):
    """The astropy download cache is keyed by this exact string: changing its
    formatting would silently force a full re-download of ~2000 cutouts."""
    row = {"t_min": 60800.5, "access_url": "https://irsa.ipac.caltech.edu/x/level2_D1.fits"}
    url, coord = gp.cutout_request(row, CATALOG["RW_AURIGAE"], 5.0)
    assert url == ("https://irsa.ipac.caltech.edu/x/level2_D1.fits"
                   f"?center={coord.ra.deg:.6f},{coord.dec.deg:.6f}&size=0.1429")
    assert gp.cutout_request(row, CATALOG["RW_AURIGAE"], 5.0)[0] == url


# --------------------------------------------------------------------------- #
# Visits
# --------------------------------------------------------------------------- #
def test_visits_split_on_gaps_and_need_enough_frames(gp):
    t = np.concatenate([60800 + np.arange(20) * 0.5, 60990 + np.arange(5) * 0.5, 61170 + np.arange(15) * 0.5])
    groups = gp.group_epochs(Table({"t_min": t}), 30.0)
    assert [len(g) for g in groups] == [20, 5, 15]
    usable = gp.pick_epochs(groups, "all", 12)
    assert [len(g) for g in usable] == [20, 15]  # the 5-frame visit is too thin
    assert [len(g) for g in gp.pick_epochs(groups, "first-last", 1)] == [20, 15]


def test_subsample_per_detector_keeps_every_detector(gp):
    rows = Table({"t_min": np.arange(30.0), "energy_bandpassname": [f"SPHEREx-D{1 + i % 6}" for i in range(30)]})
    out = gp.subsample_per_detector(rows, 1)
    assert len(out) == 6 and sorted({str(b) for b in out["energy_bandpassname"]}) == [f"SPHEREx-D{d}" for d in range(1, 7)]


def test_epoch_ids_count_months_from_the_first_visit(gp):
    visits = [Table({"t_min": [60800.0, 60801.0]}), Table({"t_min": [60983.0, 60984.0]})]
    _, ids = gp.visit_ids(visits)
    assert ids == ["2025_M0", "2025_M6"]


# --------------------------------------------------------------------------- #
# Cache-only mode
# --------------------------------------------------------------------------- #
def test_offline_sia_without_a_cached_answer_fails_clearly(gp, tmp_path, monkeypatch):
    monkeypatch.setattr(gp, "SIA_CACHE_DIR", tmp_path)
    coord = gp.position_at(CATALOG["RW_AURIGAE"], Time("2026-01-01T00:00:00"))
    with pytest.raises(gp.CacheMiss, match="--offline: no cached SIA answer"):
        gp.sia_http(coord, gp.WIDE_COLLECTIONS, 5.0, offline=True)


def test_offline_sia_replays_the_cached_answer(gp, tmp_path, monkeypatch):
    monkeypatch.setattr(gp, "SIA_CACHE_DIR", tmp_path)
    coord = gp.position_at(CATALOG["RW_AURIGAE"], Time("2026-01-01T00:00:00"))
    params = [("COLLECTION", c) for c in gp.WIDE_COLLECTIONS]
    params += [("POS", f"circle {coord.ra.deg:.7f} {coord.dec.deg:.7f} {1 / 3600:.7f}"),
               ("RESPONSEFORMAT", "VOTABLE")]
    import io

    from astropy.io.votable import from_table

    buf = io.BytesIO()
    from_table(Table({"obs_id": ["a"], "t_min": [60800.0]})).to_xml(buf)
    gp._sia_cache_file(params).write_bytes(buf.getvalue())
    rows = gp.sia_http(coord, gp.WIDE_COLLECTIONS, 5.0, offline=True)
    assert list(rows["obs_id"]) == ["a"]


def test_offline_cutouts_fail_or_skip_when_missing(gp, monkeypatch):
    rows = Table({"t_min": [60800.0, 60800.1], "access_url": ["https://x/a.fits", "https://x/b.fits"]})
    cached = {gp.cutout_request(rows[0], CATALOG["RW_AURIGAE"], 5.0)[0]}
    monkeypatch.setattr(gp, "is_url_in_cache", lambda url: url in cached)
    args = gp.default_args(offline=True)
    with pytest.raises(gp.CacheMiss, match="1/2 cutouts"):
        gp.check_cutout_cache([rows], CATALOG["RW_AURIGAE"], args)
    args.skip_missing = True
    visits, n_cached, total = gp.check_cutout_cache([rows], CATALOG["RW_AURIGAE"], args)
    assert (n_cached, total, len(visits[0])) == (1, 2, 1)
    args = gp.default_args()  # online: nothing dropped, the missing one gets downloaded
    visits, n_cached, total = gp.check_cutout_cache([rows], CATALOG["RW_AURIGAE"], args)
    assert (n_cached, total, len(visits[0])) == (1, 2, 2)
