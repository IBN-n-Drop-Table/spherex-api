from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.reports import CitizenReportCreate, Classification
from app.services.reports import (
    InMemoryReportRepository,
    ReportService,
    _seed_reports,
    build_report_service,
    get_report_service,
)

URL = "/api/v1/reports"

NEW_REPORT = {
    "discoverer": "  orbit_kid  ",
    "notes": "Small moving dot near the centre",
    "ra_deg": 133.758282,
    "dec_deg": -7.241336,
    "target_name": "wise_0855",
    "pixel_x": 312,
    "pixel_y": 293,
    "classification": "transient_anomaly",
    "anomaly_id": "anom_09",
    "snapshot_url": "https://example.org/snap.png",
}


@pytest.fixture
def service():
    return ReportService(primary=None, fallback=InMemoryReportRepository(seed=_seed_reports()))


@pytest.fixture
def client(service):
    app.dependency_overrides[get_report_service] = lambda: service
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_feed_is_seeded_and_newest_first(client):
    res = client.get(URL)
    assert res.status_code == 200
    feed = res.json()
    assert [r["target_name"] for r in feed] == ["WISE 0855", "Barnard's Star"]
    assert (feed[0]["pixel_x"], feed[0]["pixel_y"]) == (300, 300)


def test_submit_then_shows_first_in_feed(client):
    res = client.post(URL, json=NEW_REPORT)
    assert res.status_code == 201
    body = res.json()
    UUID(body["id"])
    assert body["discoverer"] == "orbit_kid"
    assert body["target_name"] == "WISE 0855"  # canonicalised
    assert body["verifications_count"] == 1

    feed = client.get(URL).json()
    assert feed[0]["id"] == body["id"]
    assert len(feed) == 3


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"target_name": "WISE 0855"}, 1),
        ({"target_name": "wise-0855"}, 1),
        ({"target_name": "barnards star"}, 1),
        ({"classification": "high_proper_motion_star"}, 1),
        ({"classification": "asteroid"}, 0),
        ({"limit": 1}, 1),
        ({"target_name": "M31"}, 0),
    ],
)
def test_feed_filters(client, params, expected):
    res = client.get(URL, params=params)
    assert res.status_code == 200
    assert len(res.json()) == expected


@pytest.mark.parametrize(
    "params", [{"limit": 0}, {"limit": 101}, {"classification": "ufo"}]
)
def test_feed_rejects_bad_params(client, params):
    assert client.get(URL, params=params).status_code == 422


@pytest.mark.parametrize(
    "patch",
    [
        {"ra_deg": 360},
        {"dec_deg": -91},
        {"discoverer": "   "},
        {"classification": "ufo"},
        {"pixel_y": None},  # pixel_x without pixel_y
        {"anomaly_id": "anom 09!"},
        {"snapshot_url": "ftp://example.org/x.png"},
        {"verifications_count": 99},  # server-controlled field
        {"id": str(uuid4())},
    ],
)
def test_submit_rejects_invalid_reports(client, patch):
    assert client.post(URL, json={**NEW_REPORT, **patch}).status_code == 422


def test_submit_minimal_report(client):
    res = client.post(
        URL, json={"discoverer": "a", "ra_deg": 0, "dec_deg": 90, "classification": "other"}
    )
    assert res.status_code == 201
    assert res.json()["target_name"] is None


def test_verify_increments(client):
    report_id = client.get(URL).json()[0]["id"]
    first = client.post(f"{URL}/{report_id}/verify").json()["verifications_count"]
    second = client.post(f"{URL}/{report_id}/verify").json()["verifications_count"]
    assert second == first + 1


def test_verify_unknown_and_malformed_ids(client):
    res = client.post(f"{URL}/{uuid4()}/verify")
    assert res.status_code == 404
    assert res.json() == {"detail": "Report not found."}
    assert client.post(f"{URL}/not-a-uuid/verify").status_code == 422


def test_verify_is_thread_safe():
    from concurrent.futures import ThreadPoolExecutor

    repo = InMemoryReportRepository(seed=_seed_reports())
    report_id = _seed_reports()[1].id
    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(lambda _: repo.verify(report_id), range(200)))
    assert repo.verify(report_id).verifications_count == 1 + 200 + 1


def test_cors_preflight_for_post(client):
    res = client.options(
        URL,
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert res.status_code == 200
    assert res.headers["access-control-allow-origin"] == "*"


class _Broken:
    def __getattr__(self, name):
        def fail(*args, **kwargs):
            raise ConnectionError("supabase down")

        return fail


def test_falls_back_to_memory_when_supabase_fails(caplog):
    service = ReportService(primary=_Broken(), fallback=InMemoryReportRepository(_seed_reports()))
    assert service.backend == "supabase"

    created = service.submit(
        CitizenReportCreate(
            discoverer="x", ra_deg=1, dec_deg=1, classification=Classification.ASTEROID
        )
    )
    assert service.backend == "memory"
    assert "Supabase unavailable" in caplog.text
    assert service.feed(limit=10)[0].id == created.id


def test_no_env_vars_means_memory_backend(caplog):
    service = build_report_service()
    assert service.backend == "memory"
    assert "SUPABASE_URL/SUPABASE_KEY not set" in caplog.text
    assert service.feed(limit=10) == []  # no fake discoveries unless asked for


def test_demo_seed_is_opt_in_and_clearly_labelled(monkeypatch):
    from dataclasses import replace

    import app.services.reports as reports

    monkeypatch.setattr(reports, "settings", replace(reports.settings, reports_demo_seed=True))
    feed = reports.build_report_service().feed(limit=10)
    assert len(feed) == 2
    for r in feed:
        assert r.is_example is True
        assert r.discoverer == reports.EXAMPLE_HANDLE
        assert r.notes.startswith(reports.EXAMPLE_PREFIX)
        assert r.snapshot_url is None  # no localhost URLs in a deployed feed
        assert r.verifications_count == 1


def test_real_reports_are_not_examples(client):
    body = client.post(URL, json=NEW_REPORT).json()
    assert body["is_example"] is False


def test_health_reports_backend(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["reports_backend"] == "memory"
    assert body["live_processing"] in {"available", "unavailable"}
