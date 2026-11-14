"""Serve-time science caveats (app/core/science_notes.py, app/services/annotations.py)."""

import pytest
from fastapi.testclient import TestClient

from app.core.science_notes import WISE_0855_NOTE
from app.main import app
from app.services.live import UpstreamUnavailable
from tests.conftest import live_client, load_raw  # noqa: F401  (fixture)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_wise_0855_sky_patch_carries_the_note(client):
    body = client.get("/api/v1/sky-patch", params={"target": "WISE_0855"}).json()
    assert body["science_note"] == WISE_0855_NOTE
    assert body["quality"] == "unvalidated"
    assert body["light_curve"] and all("Unvalidated" in p["note"] for p in body["light_curve"])


def test_the_note_says_what_was_measured():
    for fact in ("neighboring fixed star", "~11″", "1 µm", "detector 6", "7.9″", "8.05″",
                 "must not", "Y dwarf", "follows its proper motion"):
        assert fact in WISE_0855_NOTE


def test_wise_0855_target_spectrum_carries_the_note(client):
    body = client.get("/api/v1/spectrum", params={"target": "WISE_0855"}).json()
    assert body["source"] == "target"
    assert body["science_note"] == WISE_0855_NOTE and body["quality"] == "unvalidated"


def test_anomaly_spectra_are_not_about_the_target(client):
    anomaly = load_raw("wise_0855.json")["difference_imaging"]["detected_anomalies"][0]["anomaly_id"]
    body = client.get("/api/v1/spectrum", params={"target": "WISE_0855", "anomaly_id": anomaly}).json()
    assert body["source"] == "anomaly"
    assert body["science_note"] is None and body["quality"] is None


def test_job_results_are_annotated_too(client):
    body = client.post("/api/v1/jobs", json={"target": "WISE_0855"}).json()
    assert body["status"] == "completed"
    assert body["result"]["science_note"] == WISE_0855_NOTE


def test_a_fallback_showing_wise_0855_says_so(live_client):
    live_client.runner.fail = UpstreamUnavailable("timeout")
    res = live_client.post("/api/v1/jobs", json={"ra": 133.0, "dec": -7.0})  # nearest cached: WISE 0855
    result = live_client.get(f'/api/v1/jobs/{res.json()["job_id"]}').json()["result"]
    assert result["fallback"] is True and result["science_note"] == WISE_0855_NOTE


def test_clean_objects_have_no_note(client):
    body = client.get("/api/v1/sky-patch", params={"target": "EPSILON_INDI_B"}).json()
    assert body["science_note"] is None and body["quality"] is None
    assert all(p["note"] is None for p in body["light_curve"])


def test_notes_are_added_when_served_not_stored():
    raw = load_raw("wise_0855.json")
    assert "science_note" not in raw and "quality" not in raw
    assert all(p["note"] is None for p in raw["light_curve"])
