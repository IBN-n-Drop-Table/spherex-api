"""Data citation (challenge requirement): GET /credits and `data_credits` on responses."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.credits import DATA_CREDITS, SPHEREX_ACKNOWLEDGEMENT
from app.main import app
from tests.conftest import load_raw

README = (Path(__file__).resolve().parents[1] / "README.md").read_text("utf-8")
DATA_USED = README.split("## Data used", 1)[1].split("\n## ", 1)[0]


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize("path", ["/credits", "/api/v1/credits"])
def test_credits_endpoint(client, path):
    res = client.get(path)
    assert res.status_code == 200
    body = res.json()
    assert body["data_credits"] == DATA_CREDITS
    assert {c["id"] for c in body["data_credits"]} == {"spherex_dr1", "spherex_qr3", "gaia_dr3", "wise"}
    assert {c["doi"] for c in body["data_credits"] if c["doi"]} == {"10.26131/IRSA678", "10.26131/IRSA662"}
    assert body["acknowledgement"] == SPHEREX_ACKNOWLEDGEMENT


def test_citations_are_exactly_the_readmes():
    """Every DOI, URL, citation and description is written the same way in README 'Data used'."""
    for credit in DATA_CREDITS:
        for key in ("doi", "url", "citation", "access", "used_for"):
            if credit[key]:
                assert credit[key] in DATA_USED, f"{credit['id']}.{key} not in README: {credit[key]!r}"
    assert SPHEREX_ACKNOWLEDGEMENT in DATA_USED


@pytest.mark.parametrize("path, params", [
    ("/api/v1/sky-patch", {"target": "WISE_0855"}),
    ("/api/v1/sky-patch", {"target": "R_DRACONIS"}),
    ("/api/v1/spectrum", {"target": "EPSILON_INDI_B"}),
])
def test_responses_carry_the_same_credits(client, path, params):
    assert client.get(path, params=params).json()["data_credits"] == DATA_CREDITS


def test_anomaly_spectra_and_job_results_carry_them_too(client):
    anomaly = load_raw("epsilon_indi_b.json")["difference_imaging"]["detected_anomalies"][0]["anomaly_id"]
    spec = client.get("/api/v1/spectrum", params={"target": "EPSILON_INDI_B", "anomaly_id": anomaly}).json()
    assert spec["data_credits"] == DATA_CREDITS
    job = client.post("/api/v1/jobs", json={"target": "BARNARDS_STAR"}).json()
    assert job["result"]["data_credits"] == DATA_CREDITS


def test_credits_are_not_stored_in_the_data_files():
    assert "data_credits" not in load_raw("wise_0855.json")
