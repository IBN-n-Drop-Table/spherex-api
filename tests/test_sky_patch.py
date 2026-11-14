import copy
import math

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import settings
from app.core.patch_geometry import angular_separation_arcsec, png_to_world
from app.main import app
from app.schemas.sky_patch import SkyPatchResponse
from tests.conftest import load_patch

EXPECTED = load_patch("wise_0855.json")
NOT_FOUND = {"detail": "Target not found in Golden Path cache. Try WISE_0855."}
CACHED_FILES = sorted(p.name for p in settings.data_dir.glob("*.json"))


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


# --------------------------------------------------------------------------- #
# Cached targets: 200 with the exact file contents
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "params",
    [
        {"target": "WISE_0855"},
        {"target": "wise 0855"},
        {"ra": 133.795, "dec": -7.245},
        {"ra": 133.7, "dec": -7.3, "fov": 5},
        {"target": "SOMETHING_ELSE", "ra": 133.75, "dec": -7.25},
    ],
)
def test_wise_0855_returns_exact_contract(client, params):
    res = client.get("/api/v1/sky-patch", params=params)
    assert res.status_code == 200
    assert res.json() == EXPECTED


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"target": "M31"},
        {"ra": 133.75},  # dec missing -> no coordinates to query
    ],
)
def test_unknown_target_returns_404(client, params):
    res = client.get("/api/v1/sky-patch", params=params)
    assert res.status_code == 404
    assert res.json() == NOT_FOUND


@pytest.mark.parametrize("fov", [0.5, 0, 31])  # 0.5 = a degree value sent by mistake
def test_fov_is_arcmin_and_bounded(client, fov):
    res = client.get("/api/v1/sky-patch", params={"target": "WISE_0855", "fov": fov})
    assert res.status_code == 422


# --------------------------------------------------------------------------- #
# Contract of every cached file (patch WCS, formats, epochs, anomalies)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", CACHED_FILES)
def test_cached_patch_geometry(name):
    data = load_patch(name)
    patch, fov = data["patch"], data["target_info"]["fov_arcmin"]
    w, h, wcs = patch["width_px"], patch["height_px"], patch["wcs"]
    assert (wcs["ctype1"], wcs["ctype2"]) == ("RA---TAN", "DEC--TAN")
    assert (wcs["crpix1"], wcs["crpix2"]) == ((w + 1) / 2, (h + 1) / 2)
    assert math.isclose(wcs["cd1_1"], -(fov / 60) / w)
    assert math.isclose(wcs["cd2_2"], (fov / 60) / h)
    assert wcs["cd1_2"] == wcs["cd2_1"] == 0


@pytest.mark.parametrize("name", CACHED_FILES)
def test_cached_formats_and_epochs(name):
    data = load_patch(name)
    epoch_ids = [e["epoch_id"] for e in data["timelapse_epochs"]]
    assert all(e["image_format"] == "png" for e in data["timelapse_epochs"])
    diff = data["difference_imaging"]
    assert diff["image_format"] == "png"
    # Every visit is a timelapse epoch; the difference is last - first (longest baseline).
    assert len(epoch_ids) >= 2 and len(set(epoch_ids)) == len(epoch_ids)
    assert (diff["epoch_a"], diff["epoch_b"]) == (epoch_ids[-1], epoch_ids[0])


@pytest.mark.parametrize("name", CACHED_FILES)
def test_cached_anomalies(name):
    data = load_patch(name)
    patch = data["patch"]
    anomalies = data["difference_imaging"]["detected_anomalies"]
    for a in anomalies:
        # 0-based top-left pixel -> sky via patch.wcs lands on the anomaly
        # (rounding to a 0.5" PNG pixel allows up to ~0.36").
        ra, dec = png_to_world(patch["wcs"], patch["height_px"], a["pixel_x"], a["pixel_y"])
        assert angular_separation_arcsec(ra, dec, a["ra_deg"], a["dec_deg"]) < 0.5
        # Saturated anomalies (e.g. Barnard's Star itself) have no valid photometry.
        saturated = {"BLOOM", "OVERFLOW"} & set(a["contamination_flags"])
        assert a["spectrum_102_bands"] or saturated, f'{a["anomaly_id"]} has no spectrum'
        assert a["is_clean_detection"] is not bool(saturated) or not saturated
    # Duplicate contours (<= 12 px apart) were merged. Stored pixels are
    # rounded, so allow 1 px of slack against the float positions used.
    for i, a in enumerate(anomalies):
        for b in anomalies[i + 1:]:
            assert math.dist((a["pixel_x"], a["pixel_y"]), (b["pixel_x"], b["pixel_y"])) > 11


# --------------------------------------------------------------------------- #
# Schema guards
# --------------------------------------------------------------------------- #
def _broken(mutate) -> dict:
    data = copy.deepcopy(EXPECTED)
    mutate(data)
    return data


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d["difference_imaging"].update(epoch_a="2099_M0"), "epoch_a and epoch_b"),
        (lambda d: d["difference_imaging"].update(epoch_b=d["difference_imaging"]["epoch_a"]), "epoch_a and epoch_b"),
        (lambda d: d["difference_imaging"]["detected_anomalies"][0].update(pixel_x=600), "outside"),
        (lambda d: d["difference_imaging"]["detected_anomalies"][0].update(spectrum_epoch_id="x"), "spectrum_epoch_id"),
        (lambda d: d["target_info"].update(fov_arcmin=0.5), "fov_arcmin"),
        (lambda d: d["patch"]["wcs"].update(cd1_1=-d["patch"]["wcs"]["cd1_1"]), "less than 0"),
        (lambda d: d["patch"]["wcs"].update(cd1_2=1e-5), "less than or equal to 0"),
        (lambda d: d["patch"]["wcs"].update(cd2_2=d["patch"]["wcs"]["cd2_2"] * 2), "square"),
        (lambda d: d["timelapse_epochs"][0].update(image_format="jpeg"), "png"),
    ],
)
def test_schema_rejects_inconsistent_payloads(mutate, message):
    with pytest.raises(ValidationError, match=message):
        SkyPatchResponse.model_validate(_broken(mutate))


# --------------------------------------------------------------------------- #
# Live jobs (fake runner, see conftest.live_client)
# --------------------------------------------------------------------------- #
def test_uncached_coordinates_return_202_then_poll(live_client):
    res = live_client.get("/api/v1/sky-patch", params={"ra": 10.0, "dec": 41.0, "fov": 8})
    assert res.status_code == 202
    body = res.json()
    assert body["status"] == "queued" and body["kind"] == "sky_patch" and body["result"] is None
    assert res.headers["location"].endswith(f'/api/v1/jobs/{body["job_id"]}')
    assert res.headers["retry-after"] == "2"  # retry_after_ms = 1500
    assert body["retry_after_ms"] == 1500 and body["stage"] == "queued"

    job = live_client.get(f'/api/v1/jobs/{body["job_id"]}').json()
    assert job["status"] == "completed" and job["error"] is None
    assert job["result"]["target_info"]["is_cached_golden_path"] is False
    assert job["stage"] == "done" and job["progress"] == 1.0 and job["retry_after_ms"] is None
    assert job["result"]["timelapse_epochs"][0]["image_url"].startswith("http://testserver/static/")
    key, ra, dec, fov, job_id = live_client.runner.calls[0]
    assert (key, ra, dec, fov, str(job_id)) == ("LIVE", 10.0, 41.0, 8.0, body["job_id"])


def test_same_query_reuses_job_then_answers_200(live_client):
    first = live_client.get("/api/v1/sky-patch", params={"ra": 10.0, "dec": 41.0})
    second = live_client.get("/api/v1/sky-patch", params={"ra": 10.000001, "dec": 41.0})
    assert first.status_code == 202
    assert second.status_code == 200  # job already completed: result served directly
    assert second.json()["target_info"]["is_cached_golden_path"] is False
    assert len(live_client.runner.calls) == 1


def test_uncached_catalogue_target_runs_with_its_proper_motion(live_client):
    uncached = sorted(k for k in ("RW_AURIGAE", "R_DRACONIS")
                      if not (settings.data_dir / f"{k.lower()}.json").exists())
    if not uncached:
        pytest.skip("every catalogue target is cached")
    res = live_client.get("/api/v1/sky-patch", params={"target": uncached[0]})
    assert res.status_code == 202
    assert live_client.runner.calls[0][0] == uncached[0]


def test_failed_job_reports_error_and_can_retry(live_client):
    live_client.runner.fail = RuntimeError("Need two visits with >= 12 exposures each")
    job_id = live_client.get("/api/v1/sky-patch", params={"ra": 1.0, "dec": 1.0}).json()["job_id"]
    job = live_client.get(f"/api/v1/jobs/{job_id}").json()
    assert job["status"] == "failed"
    assert "Need two visits" in job["error"]

    live_client.runner.fail = None
    retry = live_client.get("/api/v1/sky-patch", params={"ra": 1.0, "dec": 1.0})
    assert retry.status_code == 202 and retry.json()["job_id"] != job_id


def test_unknown_job_is_404(live_client):
    res = live_client.get("/api/v1/jobs/00000000-0000-0000-0000-000000000000")
    assert res.status_code == 404
    assert live_client.get("/api/v1/jobs/not-a-uuid").status_code == 422


def test_full_queue_returns_503(live_client):
    from app.schemas.jobs import JobKind

    for i in range(8):  # fill the queue without running anything
        live_client.jobs.find_or_create(JobKind.SKY_PATCH, ("filler", i))
    res = live_client.get("/api/v1/sky-patch", params={"ra": 50.0, "dec": 5.0})
    assert res.status_code == 503
    assert res.headers["retry-after"] == "30"


# --------------------------------------------------------------------------- #
# CORS
# --------------------------------------------------------------------------- #
def test_cors_allows_any_origin(client):
    res = client.get(
        "/api/v1/sky-patch",
        params={"target": "WISE_0855"},
        headers={"Origin": "http://localhost:5173"},
    )
    assert res.headers["access-control-allow-origin"] == "*"


def test_202_exposes_location_header_to_browsers(live_client):
    res = live_client.get(
        "/api/v1/sky-patch", params={"ra": 20.0, "dec": 20.0}, headers={"Origin": "http://localhost:5173"}
    )
    exposed = {h.strip().lower() for h in res.headers["access-control-expose-headers"].split(",")}
    assert {"location", "retry-after"} <= exposed


@pytest.mark.parametrize("origin", [None, "http://localhost:5173"])
def test_static_images_are_cors_readable(client, origin):
    png = next((settings.static_dir / "golden_path").glob("*.png")).name
    headers = {"Origin": origin} if origin else {}
    res = client.get(f"/static/golden_path/{png}", headers=headers)
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/png"
    assert res.headers["access-control-allow-origin"] == "*"
    assert res.headers["cross-origin-resource-policy"] == "cross-origin"
