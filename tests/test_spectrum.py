import pytest

from tests.conftest import load_patch

URL = "/api/v1/spectrum"
WISE = load_patch("wise_0855.json")
ANOMALIES = WISE["difference_imaging"]["detected_anomalies"]


def test_target_spectrum(live_client):
    res = live_client.get(URL, params={"target": "wise_0855"})
    assert res.status_code == 200
    body = res.json()
    assert body["source"] == "target" and body["is_cached_golden_path"] is True
    assert body["spectrum_102_bands"] == WISE["spectrum_102_bands"]
    assert (body["ra_deg"], body["dec_deg"]) == (WISE["patch"]["wcs"]["crval1"], WISE["patch"]["wcs"]["crval2"])
    assert body["epoch_id"] == WISE["timelapse_epochs"][0]["epoch_id"]


@pytest.mark.parametrize("anomaly", ANOMALIES, ids=lambda a: a["anomaly_id"])
def test_every_anomaly_spectrum_is_precomputed(live_client, anomaly):
    res = live_client.get(URL, params={"target": "WISE_0855", "anomaly_id": anomaly["anomaly_id"]})
    assert res.status_code == 200
    body = res.json()
    assert body["source"] == "anomaly"
    assert body["spectrum_102_bands"] == anomaly["spectrum_102_bands"]
    assert body["epoch_id"] == anomaly["spectrum_epoch_id"]
    assert live_client.runner.calls == []  # never touches the live pipeline


def test_anomaly_by_coordinates(live_client):
    a = ANOMALIES[0]
    res = live_client.get(URL, params={"ra": a["ra_deg"] + 0.0005, "dec": a["dec_deg"]})  # ~1.8"
    assert res.status_code == 200
    assert res.json()["anomaly_id"] == a["anomaly_id"]


def test_target_by_coordinates_uses_epoch_position_not_catalogue(live_client):
    wcs = WISE["patch"]["wcs"]
    res = live_client.get(URL, params={"ra": wcs["crval1"], "dec": wcs["crval2"]})
    assert res.status_code == 200 and res.json()["source"] == "target"


def test_unknown_anomaly_is_404(live_client):
    res = live_client.get(URL, params={"target": "WISE_0855", "anomaly_id": "anom_99"})
    assert res.status_code == 404
    assert "anom_99" in res.json()["detail"]


def test_uncached_position_queues_live_spectrum(live_client):
    res = live_client.get(URL, params={"ra": 200.0, "dec": -30.0})
    assert res.status_code == 202
    body = res.json()
    assert body["kind"] == "spectrum"
    job = live_client.get(f'/api/v1/jobs/{body["job_id"]}').json()
    assert job["status"] == "completed"
    assert job["result"]["source"] == "target" and job["result"]["is_cached_golden_path"] is False


def test_spectrum_and_sky_patch_jobs_are_separate(live_client):
    params = {"ra": 200.0, "dec": -30.0}
    a = live_client.get(URL, params=params).json()["job_id"]
    b = live_client.get("/api/v1/sky-patch", params=params).json()["job_id"]
    assert a != b


@pytest.mark.parametrize(
    ("params", "code"),
    [
        ({}, 422),
        ({"ra": 1.0}, 422),
        ({"target": "M31"}, 404),
        ({"ra": 200.0, "dec": -30.0, "anomaly_id": "anom_01"}, 404),
        ({"target": "WISE_0855", "radius_arcsec": 0}, 422),
        ({"target": "WISE_0855", "fov": 0.5}, 422),
    ],
)
def test_bad_spectrum_queries(live_client, params, code):
    assert live_client.get(URL, params=params).status_code == code
