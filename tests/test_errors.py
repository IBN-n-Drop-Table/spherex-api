"""Every error has the same shape: {"detail": str, "code": str, "errors": list | null}."""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _assert_shape(body: dict) -> None:
    assert set(body) == {"detail", "code", "errors"}
    assert isinstance(body["detail"], str) and body["detail"]
    assert isinstance(body["code"], str) and body["code"]


@pytest.mark.parametrize("path, params, status, code", [
    ("/api/v1/sky-patch", {"target": "M31"}, 404, "not_found"),
    ("/api/v1/sky-patch", {"ra": 400, "dec": 0}, 422, "validation_error"),  # our own 422
    ("/api/v1/sky-patch", {"target": "WISE_0855", "fov": 99}, 422, "validation_error"),  # FastAPI's 422
    ("/api/v1/sky-patch", {"target": "WISE_0855", "band": "XYZ"}, 422, "validation_error"),
    ("/api/v1/spectrum", {}, 422, "validation_error"),
    ("/api/v1/jobs/not-a-uuid", {}, 422, "validation_error"),
    ("/api/v1/jobs/00000000-0000-0000-0000-000000000000", {}, 404, "not_found"),
    ("/api/v1/epochs", {"ra": 1, "dec": 999}, 422, "validation_error"),
    ("/api/v1/no-such-route", {}, 404, "not_found"),
])
def test_error_shape_is_uniform(client, path, params, status, code):
    res = client.get(path, params=params)
    assert res.status_code == status
    body = res.json()
    _assert_shape(body)
    assert body["code"] == code


def test_validation_errors_are_readable_and_listed(client):
    body = client.get("/api/v1/sky-patch", params={"target": "WISE_0855", "fov": 99}).json()
    assert body["detail"].startswith("fov: ")  # "fov: Input should be less than or equal to 30"
    assert body["errors"] == [{"loc": ["query", "fov"], "msg": body["errors"][0]["msg"],
                               "type": "less_than_equal"}]


def test_body_validation_errors(client):
    res = client.post("/api/v1/jobs", json={"target": "X", "evil": 1})
    assert res.status_code == 422
    body = res.json()
    _assert_shape(body)
    assert body["errors"][0]["loc"] == ["body", "evil"] and "evil" in body["detail"]


def test_wrong_method_keeps_the_shape(client):
    res = client.delete("/api/v1/targets")
    assert res.status_code == 405
    _assert_shape(res.json())
    assert res.json()["code"] == "method_not_allowed"
