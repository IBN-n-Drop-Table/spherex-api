"""API behaviour added for the demo: jobs with progress, offline-first fallback,
sexagesimal coordinates, band echo, /epochs, /targets, relative URLs on disk."""

import pytest

from app.core.config import settings
from app.services.fallback import FALLBACK_MESSAGE
from app.services.live import LiveProcessingUnavailable, UpstreamUnavailable
from tests.conftest import load_raw

CACHED_FILES = sorted(p.name for p in settings.data_dir.glob("*.json"))
WISE_SEXA = {"ra": "08h55m10.831s", "dec": "-7d14m42.53s"}


def _image_urls(payload):
    for e in payload["timelapse_epochs"]:
        yield e["image_url"]
        yield from e["chemical_filters"].values()
    yield payload["difference_imaging"]["subtracted_image_url"]


# --------------------------------------------------------------------------- #
# Coordinates, band, URLs
# --------------------------------------------------------------------------- #
def test_sexagesimal_coordinates_hit_the_cache(live_client):
    res = live_client.get("/api/v1/sky-patch", params={**WISE_SEXA, "fov": 5})
    assert res.status_code == 200
    assert res.json()["target_info"]["name"] == "WISE 0855"
    assert live_client.runner.calls == []


@pytest.mark.parametrize("params", [{"ra": "25h", "dec": "0"}, {"ra": "10", "dec": "-95"}, {"ra": "x", "dec": "1"}])
def test_bad_coordinates_are_422(live_client, params):
    assert live_client.get("/api/v1/sky-patch", params=params).status_code == 422


def test_band_is_echoed_and_never_changes_the_shape(live_client):
    plain = live_client.get("/api/v1/sky-patch", params={"target": "WISE_0855"}).json()
    co2 = live_client.get("/api/v1/sky-patch", params={"target": "WISE_0855", "band": "CO2"}).json()
    assert co2["band"] == "CO2" and plain["band"] is None
    assert {**co2, "band": None} == plain
    assert live_client.get("/api/v1/sky-patch", params={"target": "WISE_0855", "band": "O3"}).status_code == 422


def test_urls_are_absolute_for_clients(live_client):
    body = live_client.get("/api/v1/sky-patch", params={"target": "WISE_0855"}).json()
    assert all(u.startswith("http://testserver/static/") for u in _image_urls(body))


def test_public_base_overrides_request_host(live_client, monkeypatch):
    import dataclasses

    from app.routers import common

    monkeypatch.setattr(common, "settings",
                        dataclasses.replace(common.settings, public_base="https://spherex.example.org/static"))
    body = live_client.get("/api/v1/sky-patch", params={"target": "WISE_0855"}).json()
    assert all(u.startswith("https://spherex.example.org/static/") for u in _image_urls(body))


@pytest.mark.parametrize("name", CACHED_FILES)
def test_cached_files_store_relative_image_paths(name):
    assert all(u.startswith("/static/golden_path/") for u in _image_urls(load_raw(name)))


@pytest.mark.parametrize("name", CACHED_FILES)
def test_light_curves_never_fake_zeros(name):
    for point in load_raw(name)["light_curve"]:
        if point["flux_mjy_sr"] is None:
            assert point["note"], "a null flux must say why"
        else:
            assert not (point["flux_mjy_sr"] == 0 and point["variance"] == 0)


@pytest.mark.parametrize("name", CACHED_FILES)
def test_empty_spectrum_explains_itself(name):
    raw = load_raw(name)
    if not raw["spectrum_102_bands"]:
        assert raw.get("spectrum_message")


# --------------------------------------------------------------------------- #
# POST /jobs
# --------------------------------------------------------------------------- #
def test_post_job_for_cached_target_is_done_at_once(live_client):
    res = live_client.post("/api/v1/jobs", json={"target": "WISE_0855", "band": "H2O"})
    assert res.status_code == 200
    job = res.json()
    assert job["status"] == "completed" and job["stage"] == "done" and job["progress"] == 1.0
    assert job["result"]["band"] == "H2O" and job["result"]["target_info"]["is_cached_golden_path"]
    again = live_client.get(f'/api/v1/jobs/{job["job_id"]}').json()
    assert again["status"] == "completed"


def test_post_job_live_reports_progress_and_reuses_the_job(live_client):
    body = {"ra": "00h40m00s", "dec": "+41d00m00s", "fov": 6}
    res = live_client.post("/api/v1/jobs", json=body)
    assert res.status_code == 202
    job = res.json()
    assert job["retry_after_ms"] == 1500 and job["message"]
    assert res.headers["location"].endswith(f'/api/v1/jobs/{job["job_id"]}')
    done = live_client.get(f'/api/v1/jobs/{job["job_id"]}').json()
    assert done["status"] == "completed" and done["result"]["fallback"] is False
    second = live_client.post("/api/v1/jobs", json=body).json()
    assert second["job_id"] == job["job_id"]  # same patch = same job
    assert len(live_client.runner.calls) == 1
    assert live_client.runner.calls[0][1:4] == (10.0, 41.0, 6.0)


def test_post_job_needs_a_position(live_client):
    assert live_client.post("/api/v1/jobs", json={}).status_code == 422
    assert live_client.post("/api/v1/jobs", json={"target": "M31"}).status_code == 404


# --------------------------------------------------------------------------- #
# Offline-first fallback: never a 500 because of IRSA
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("error, reason", [
    (UpstreamUnavailable("NASA/IRSA is unavailable or busy right now (HTTP 502)."), "irsa_unavailable"),
    (LiveProcessingUnavailable("science stack not installed"), "live_processing_unavailable"),
])
def test_irsa_outage_completes_with_cached_fallback(live_client, error, reason):
    live_client.runner.fail = error
    res = live_client.post("/api/v1/jobs", json={"ra": 133.0, "dec": -7.0})
    job = live_client.get(f'/api/v1/jobs/{res.json()["job_id"]}').json()
    assert job["status"] == "completed" and job["error"] is None
    result = job["result"]
    assert result["fallback"] is True and result["fallback_message"].startswith(FALLBACK_MESSAGE)
    assert result["fallback_reason"] == reason
    assert result["target_info"]["name"] == "WISE 0855"  # nearest cached target to (133, -7)
    assert all(u.startswith("http://testserver/static/") for u in _image_urls(result))


def test_far_away_fallback_says_which_target_it_shows(live_client):
    live_client.runner.fail = UpstreamUnavailable("timeout")
    res = live_client.get("/api/v1/sky-patch", params={"ra": 10.0, "dec": 41.0})
    job = live_client.get(f'/api/v1/jobs/{res.json()["job_id"]}').json()
    assert job["result"]["fallback"] is True
    assert "más cercano" in job["result"]["fallback_message"]


def test_spectrum_fallback(live_client):
    live_client.runner.fail = UpstreamUnavailable("HTTP 429")
    res = live_client.get("/api/v1/spectrum", params={"ra": 10.0, "dec": 41.0})
    job = live_client.get(f'/api/v1/jobs/{res.json()["job_id"]}').json()
    assert job["status"] == "completed" and job["result"]["fallback"] is True
    assert job["result"]["fallback_reason"] == "irsa_unavailable"


def test_other_errors_still_fail_the_job_without_500(live_client):
    live_client.runner.fail = RuntimeError("Need two visits with >= 12 exposures each; got [3]")
    res = live_client.post("/api/v1/jobs", json={"ra": 10.0, "dec": 41.0})
    job = live_client.get(f'/api/v1/jobs/{res.json()["job_id"]}')
    assert job.status_code == 200
    assert job.json()["status"] == "failed" and "Need two visits" in job.json()["error"]


# --------------------------------------------------------------------------- #
# /epochs and /targets
# --------------------------------------------------------------------------- #
def test_epochs_for_cached_target_are_instant(live_client):
    body = live_client.get("/api/v1/epochs", params=WISE_SEXA).json()
    assert body["is_cached_golden_path"] is True and body["fallback"] is False
    assert body["fallback_reason"] is None
    assert [e["epoch_id"] for e in body["epochs"]] == [
        e["epoch_id"] for e in load_raw("wise_0855.json")["timelapse_epochs"]]


def test_epochs_live_and_fallback(live_client):
    live_client.runner.epochs_result = [{"epoch_id": "2025_M0", "date": "2025-08-21T00:00:00Z",
                                         "date_start": "2025-08-15", "date_end": "2025-10-01",
                                         "n_images": 118, "n_per_detector": {"D1": 20}}]
    body = live_client.get("/api/v1/epochs", params={"ra": 10, "dec": 41}).json()
    assert body["is_cached_golden_path"] is False and body["n_images"] == 118

    live_client.runner.epochs_result = UpstreamUnavailable("IRSA SIA did not answer within 5 s")
    res = live_client.get("/api/v1/epochs", params={"ra": 10, "dec": 41})
    assert res.status_code == 200 and res.json()["fallback"] is True
    assert res.json()["fallback_reason"] == "irsa_unavailable"


def test_targets_lists_the_five_challenge_objects(live_client):
    targets = live_client.get("/api/v1/targets").json()
    assert {t["key"] for t in targets} == {"WISE_0855", "BARNARDS_STAR", "EPSILON_INDI_B", "RW_AURIGAE", "R_DRACONIS"}
    assert all(t["sky_patch_query"].startswith("/api/v1/sky-patch?target=") for t in targets)
