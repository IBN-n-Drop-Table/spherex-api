import json

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app

EXPECTED = json.loads((settings.data_dir / "wise_0855.json").read_text("utf-8"))
NOT_FOUND = {"detail": "Target not found in Golden Path cache. Try WISE_0855."}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


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
        {"ra": 10.0, "dec": 41.0},
        {"ra": 133.75},  # dec missing -> no coordinate match
    ],
)
def test_unknown_target_returns_404(client, params):
    res = client.get("/api/v1/sky-patch", params=params)
    assert res.status_code == 404
    assert res.json() == NOT_FOUND


def test_cors_allows_any_origin(client):
    res = client.get(
        "/api/v1/sky-patch",
        params={"target": "WISE_0855"},
        headers={"Origin": "http://localhost:5173"},
    )
    assert res.headers["access-control-allow-origin"] == "*"
