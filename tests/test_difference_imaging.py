"""Synthetic checks of the epoch difference and the anomaly detector.

These exercise the real pipeline code (scripts/generate_golden_path.py), so
they need the science stack (requirements-science.txt). Without it they are
skipped, like the rest of the live-processing code.
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
from astropy.coordinates import SkyCoord  # noqa: E402

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "generate_golden_path.py"
N = 50  # stacking grid of a 5' patch (6" pixels)
SIGMA_PX = 0.8  # SPHEREx-like PSF: FWHM ~1.9 native px
NOISE = 1.0


@pytest.fixture(scope="module")
def gp():
    name = "spherex_golden_path_pipeline_tests"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    module.quiet_astropy()
    return module


@pytest.fixture(scope="module")
def ref_wcs(gp):
    wcs, shape = gp.build_reference_wcs(SkyCoord(150.0, 2.0, unit="deg"), 5.0, 600)
    assert shape == (N, N)
    return wcs


def star(x0, y0, amp=200.0, sigma=SIGMA_PX):
    yy, xx = np.indices((N, N))
    return amp * np.exp(-((xx - x0) ** 2 + (yy - y0) ** 2) / (2 * sigma**2))


def field(stars, rng=None, noise=NOISE):
    img = sum((star(x, y, a) for x, y, a in stars), np.zeros((N, N)))
    if rng is not None:
        img = img + rng.normal(0, noise, (N, N))
    return img


def epoch(gp, epoch_id):
    return gp.EpochProduct(epoch_id=epoch_id, when=datetime(2025, 1, 1, tzinfo=UTC), exposures=[])


def detect(gp, ref_wcs, img1, img2, noise=NOISE):
    diff = img2 - img1
    var = np.full((N, N), 2 * noise**2)
    return gp.detect_anomalies(diff, var, epoch(gp, "E1"), epoch(gp, "E2"), ref_wcs, gp.default_args())


def png_to_grid(a):
    """Anomaly PNG pixel (0-based, top-left, 12x upscale) -> grid (x, y), FITS orientation."""
    return (a["pixel_x"] + 0.5) / 12 - 0.5, N - 0.5 - (a["pixel_y"] + 0.5) / 12


def test_identical_epochs_subtract_to_exactly_zero(gp):
    rng = np.random.default_rng(1)
    img = field([(20, 30, 300), (35, 12, 80)], rng)
    var = np.ones((N, N))
    s1, _ = gp.median_stack([img, img + 0.0], [var, var])
    s2, _ = gp.median_stack([img.copy()], [var])
    assert np.array_equal(s1, s2)
    assert np.all(s2 - s1 == 0)


def test_identical_epochs_give_no_detections(gp, ref_wcs):
    rng = np.random.default_rng(2)
    img = field([(20, 30, 300), (35, 12, 80)], rng)
    assert detect(gp, ref_wcs, img, img.copy()) == []


def test_pure_noise_gives_no_detections(gp, ref_wcs):
    for seed in range(10):
        rng = np.random.default_rng(100 + seed)
        assert detect(gp, ref_wcs, rng.normal(0, NOISE, (N, N)), rng.normal(0, NOISE, (N, N))) == []


def test_pedestal_is_removed_before_subtracting(gp):
    """A per-visit sky offset (ZODI model error) must not survive the stack."""
    rng = np.random.default_rng(3)
    base = field([(25, 25, 300)])
    var = np.ones((N, N))
    s1, _ = gp.median_stack([base + rng.normal(0, 0.1, (N, N)) + 5.0], [var])
    s2, _ = gp.median_stack([base + rng.normal(0, 0.1, (N, N)) - 3.0], [var])
    far = np.ones((N, N), bool)
    far[15:36, 15:36] = False
    assert abs(float(np.median((s2 - s1)[far]))) < 0.05


@pytest.mark.parametrize("shift_px", [4.0, 6.0])
def test_moving_star_gives_dipole_with_correct_sign_and_separation(gp, ref_wcs, shift_px):
    """Shift >> PSF: the lobes are the two star positions. Positive = new position."""
    rng = np.random.default_rng(4)
    x1, y1 = 20.0, 25.0
    x2, y2 = x1 + shift_px, y1
    found = detect(gp, ref_wcs, field([(x1, y1, 300)], rng), field([(x2, y2, 300)], rng))
    kinds = {a["kind"]: png_to_grid(a) for a in found}
    assert set(kinds) == {"appeared", "disappeared"}
    (ax, ay), (dx, dy) = kinds["appeared"], kinds["disappeared"]
    assert math.hypot(ax - x2, ay - y2) < 0.5
    assert math.hypot(dx - x1, dy - y1) < 0.5
    assert math.hypot(ax - dx, ay - dy) == pytest.approx(shift_px, abs=0.5)


def test_sub_psf_shift_dipole_lobes_overestimate_motion(gp, ref_wcs):
    """Documented limitation: for a shift smaller than the PSF (all 3 high proper
    motion targets move 1.1-1.7 native px between first and last visit), the lobe
    centroids sit about one PSF apart, NOT at the true displacement. Their sign
    and direction are right; their separation is not a proper-motion measurement."""
    rng = np.random.default_rng(5)
    shift = 1.2
    found = detect(gp, ref_wcs, field([(25, 25, 400)], rng), field([(25 + shift, 25, 400)], rng))
    kinds = {a["kind"]: png_to_grid(a) for a in found}
    assert set(kinds) == {"appeared", "disappeared"}
    (ax, ay), (dx, dy) = kinds["appeared"], kinds["disappeared"]
    assert ax > dx  # direction is right
    assert abs(ay - dy) < 0.5
    assert ax - dx > 1.5 * shift  # but the lobes overestimate the shift


def test_one_pixel_misalignment_is_reported(gp, ref_wcs):
    """A whole-field 1 px registration error turns every bright fixed star into a
    dipole. The detector reports it (one pair per star); nothing in the pipeline
    checks registration explicitly, so this is the only signal."""
    rng = np.random.default_rng(6)
    stars = [(12, 12, 300), (37, 14, 250), (14, 36, 280), (36, 37, 260)]
    shifted = [(x + 1.0, y, a) for x, y, a in stars]
    found = detect(gp, ref_wcs, field(stars, rng), field(shifted, rng))
    near = {i for a in found for i, (x, y, _) in enumerate(stars)
            if math.hypot(png_to_grid(a)[0] - x - 0.5, png_to_grid(a)[1] - y) < 2.5}
    assert near == {0, 1, 2, 3}
    assert {a["kind"] for a in found} == {"appeared", "disappeared"}


def test_anomaly_coordinates_round_trip_to_sky(gp, ref_wcs):
    rng = np.random.default_rng(7)
    x, y = 30.0, 18.0
    found = detect(gp, ref_wcs, field([], rng), field([(x, y, 300)], rng))
    assert len(found) == 1 and found[0]["kind"] == "appeared"
    world = ref_wcs.pixel_to_world(x, y)
    sep = world.separation(SkyCoord(found[0]["ra_deg"], found[0]["dec_deg"], unit="deg")).arcsec
    assert sep < 3.0  # half a native pixel


def test_a_single_visit_is_not_enough_for_a_difference(gp):
    from astropy.table import Table

    one = Table({"t_min": np.linspace(60800.0, 60801.0, 20)})
    assert gp.pick_epochs(gp.group_epochs(one, 30.0), "all", 12) is None
    two = Table({"t_min": np.concatenate([np.linspace(60800, 60801, 20), np.linspace(60990, 60991, 20)])})
    visits = gp.pick_epochs(gp.group_epochs(two, 30.0), "all", 12)
    assert len(visits) == 2 and visits[0]["t_min"].max() < visits[1]["t_min"].min()


# --------------------------------------------------------------------------- #
# Consecutive-visit (pairwise) differences, end to end through build_payload
# --------------------------------------------------------------------------- #
from astropy.table import Table  # noqa: E402

from app.core.targets import CATALOG  # noqa: E402
from app.core.urls import to_internal  # noqa: E402
from app.schemas.sky_patch import SkyPatchResponse  # noqa: E402

MJD_2025 = 60676.0  # 2025-01-01


class MemoryUploader:
    def __init__(self):
        self.files: dict[str, bytes] = {}

    def upload_png(self, filename, data):
        self.files[filename] = data
        return f"/static/golden_path/{filename}"


def synthetic_visits(day_offsets, frames=None):
    """One astropy Table per visit. frames[i] = {detector: n_frames} (default 6 each)."""
    visits = []
    for i, day in enumerate(day_offsets):
        per_det = (frames or {}).get(i, {d: 6 for d in range(1, 7)})
        rows = [(MJD_2025 + day + 0.01 * k, f"SPHEREx-D{d}", f"obs_{i}_{d}_{k}")
                for d, n in per_det.items() for k in range(n)]
        visits.append(Table(rows=rows, names=("t_min", "energy_bandpassname", "obs_id")))
    return visits


@pytest.fixture
def run_payload(gp, monkeypatch):
    """build_payload on synthetic visits: a field star plus the target moving with
    its catalogue proper motion. No network, no cache."""

    def run(target, day_offsets, frames=None, seed=0):
        rng = np.random.default_rng(seed)
        visits = synthetic_visits(day_offsets, frames)
        monkeypatch.setattr(gp, "find_epoch_rows", lambda t, a: visits)

        def process_rows(rows, tgt, ref_wcs, ref_shape, args, label, counter=None):
            out = []
            for row in rows:
                when = gp.Time(float(row["t_min"]), format="mjd", scale="utc")
                x, y = (float(v) for v in ref_wcs.world_to_pixel(gp.position_at(tgt, when)))
                img = star(x, y, 400) + star(10, 40, 300) + rng.normal(0, NOISE, ref_shape)
                det = int(str(row["energy_bandpassname"]).rsplit("D", 1)[-1])
                out.append(gp.Exposure(
                    obs_id=str(row["obs_id"]), detector=det, mjd=float(row["t_min"]),
                    wavelength_um=1.0 + 0.6 * det, bandwidth_um=0.02, image=img, display=img.copy(),
                    variance=np.full(ref_shape, NOISE**2), flags=np.zeros(ref_shape),
                    wave_map=np.full(ref_shape, 1.0 + 0.6 * det, np.float32),
                    bw_map=np.full(ref_shape, 0.02, np.float32), flag_names={},
                    ap_flux=None, ap_var=None))
            return sorted(out, key=lambda e: e.mjd)

        monkeypatch.setattr(gp, "process_rows", process_rows)
        stages, uploader = [], MemoryUploader()
        payload = gp.build_payload(target, uploader, gp.default_args(no_cache=True), cached=False,
                                   progress=lambda stage, frac, msg: stages.append(stage))
        SkyPatchResponse.model_validate(to_internal(payload))  # the contract holds
        return payload, uploader, stages

    return run


def old_render_difference(gp, diff, size):
    """The first-vs-last renderer exactly as it was before pairwise differences."""
    from matplotlib import colormaps

    finite = diff[np.isfinite(diff)]
    lim = float(np.percentile(np.abs(finite), 99.5)) if finite.size else 1.0
    lim = lim or 1.0
    rgba = colormaps["RdBu_r"]((np.clip(diff / lim, -1, 1) + 1) / 2)
    rgba[~np.isfinite(diff)] = (0, 0, 0, 1)
    return gp.to_png(rgba[..., :3], size)


def test_render_difference_default_is_unchanged(gp):
    rng = np.random.default_rng(11)
    diff = rng.normal(0, 1, (N, N))
    diff[3, 4] = np.nan
    assert gp.render_difference(diff, 600) == old_render_difference(gp, diff, 600)


def test_two_visits_give_no_pairs_and_the_same_first_vs_last(gp, run_payload):
    payload, uploader, stages = run_payload(CATALOG["BARNARDS_STAR"], [0, 365])
    diff = payload["difference_imaging"]
    assert diff["pairwise"] == []
    assert "difference" not in stages
    assert [n for n in uploader.files if "_diff" in n] == ["barnardsstar_diff.png"]
    assert diff["kind"] == "first_vs_last"
    assert (diff["epoch_a"], diff["epoch_b"]) == (payload["timelapse_epochs"][-1]["epoch_id"],
                                                  payload["timelapse_epochs"][0]["epoch_id"])


@pytest.mark.parametrize("n_visits", [3, 4])
def test_consecutive_pairs_in_time_order(gp, run_payload, n_visits):
    days = [0, 180, 365, 545][:n_visits]
    payload, uploader, stages = run_payload(CATALOG["BARNARDS_STAR"], days)
    ids = [e["epoch_id"] for e in payload["timelapse_epochs"]]
    pairs = payload["difference_imaging"]["pairwise"]
    assert [(p["epoch_b"], p["epoch_a"]) for p in pairs] == list(zip(ids, ids[1:]))
    assert all(p["kind"] == "consecutive" for p in pairs)
    assert [p["delta_t_days"] for p in pairs] == pytest.approx([b - a for a, b in zip(days, days[1:])], abs=0.1)
    assert sorted(n for n in uploader.files if "_diff_" in n) == \
        sorted(f"barnardsstar_diff_epoch{i}_epoch{i + 1}.png" for i in range(1, n_visits))
    assert all(p["subtracted_image_url"].endswith(f"_epoch{i + 1}_epoch{i + 2}.png") for i, p in enumerate(pairs))
    assert "difference" in stages
    # anomalies are only detected on the first-vs-last difference
    assert all("detected_anomalies" not in p for p in pairs)


def test_each_pair_uses_the_detectors_those_two_visits_share(gp, run_payload):
    full = {d: 6 for d in range(1, 7)}
    no_det1 = {d: 6 for d in range(2, 7)}
    thin_det4 = {**full, 4: 3}  # 3 frames: below PAIR_MIN_PER_DETECTOR
    payload, *_ = run_payload(CATALOG["EPSILON_INDI_B"], [0, 180, 365, 545],
                              frames={0: full, 1: thin_det4, 2: no_det1, 3: full})
    pairs = payload["difference_imaging"]["pairwise"]
    assert pairs[0]["detectors_used"] == [1, 2, 3, 5, 6]  # det 4 too thin in visit 2
    assert pairs[1]["detectors_used"] == [2, 3, 5, 6]  # det 1 missing in visit 3, det 4 thin
    assert pairs[2]["detectors_used"] == [2, 3, 4, 5, 6]  # det 1 missing in visit 3
    # first vs last keeps its own rule: every detector both share
    assert payload["difference_imaging"]["detectors_used"] == [1, 2, 3, 4, 5, 6]


def test_pairs_fall_back_to_every_shared_detector_when_all_are_thin(gp, run_payload):
    one_each = {d: 1 for d in range(1, 7)}  # live jobs: 1 frame per detector
    payload, *_ = run_payload(CATALOG["BARNARDS_STAR"], [0, 180, 365], frames={0: one_each, 1: one_each, 2: one_each})
    assert all(p["detectors_used"] == [1, 2, 3, 4, 5, 6] for p in payload["difference_imaging"]["pairwise"])


def test_weak_signal_flags_sub_pixel_expected_motion(gp, run_payload):
    # Barnard: 10.4"/yr. 180 d -> ~5.1" (< 6.15" pixel): weak; 365 d -> ~10.4": not weak.
    payload, *_ = run_payload(CATALOG["BARNARDS_STAR"], [0, 180, 545])
    diff = payload["difference_imaging"]
    p1, p2 = diff["pairwise"]
    assert p1["expected_shift_arcsec"] == pytest.approx(10.39 * 180 / 365.25, rel=0.02) and p1["weak_signal"]
    assert p2["expected_shift_arcsec"] == pytest.approx(10.39 * 365 / 365.25, rel=0.02) and not p2["weak_signal"]
    assert diff["expected_shift_arcsec"] > 15 and diff["weak_signal"] is False
    payload, *_ = run_payload(CATALOG["R_DRACONIS"], [0, 180, 365])  # no proper motion
    assert all(p["weak_signal"] for p in payload["difference_imaging"]["pairwise"])


def test_pair_images_use_a_background_scale(gp):
    """+/-5 sigma of the background, not the 99.5th percentile: a bright residual
    can no longer wash the field out to white."""
    rng = np.random.default_rng(12)
    diff = rng.normal(0, 1.0, (N, N))
    diff[25, 25] = 1e4  # a saturated core
    sigma = gp.background_sigma(diff)
    assert sigma == pytest.approx(1.0, rel=0.15)
    assert gp.render_difference(diff, 600, 5 * sigma) != gp.render_difference(diff, 600)


def test_sky_background_removes_a_gradient_but_keeps_point_sources(gp):
    yy, xx = np.indices((N, N))
    gradient = 0.05 * (xx - 25)
    src = star(25, 25, 300)
    diff = gradient + src
    sky = gp.sky_background(diff, [src, src])
    residual = diff - sky
    far = np.hypot(xx - 25, yy - 25) > 6
    assert np.abs(residual[far]).max() < 0.3 * np.abs(gradient[far]).max()
    assert residual[25, 25] == pytest.approx(300, rel=0.05)  # the star is untouched


def test_single_visit_still_fails_with_a_clear_message(gp, monkeypatch):
    one = Table({"t_min": np.linspace(60800.0, 60801.0, 20), "obs_collection": ["spherex_qr3"] * 20,
                 "obs_id": [f"o{i}" for i in range(20)], "energy_bandpassname": ["SPHEREx-D1"] * 20,
                 "dataproduct_subtype": ["science"] * 20})
    monkeypatch.setattr(gp, "sia_http", lambda *a, **k: one.copy())
    with pytest.raises(RuntimeError, match="Need two visits"):
        gp.find_epoch_rows(CATALOG["RW_AURIGAE"], gp.default_args())
