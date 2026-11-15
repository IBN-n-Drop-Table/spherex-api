"""Global pace of queries to SIMBAD: token bucket, fallbacks while it is empty, and the user-facing texts.

No network: SIMBAD is a fake transport, and httpx.post is replaced by a function that fails the test.
"""

import math

import pytest
from fastapi.testclient import TestClient

from app.core.simbad_settings import SimbadSettings
from app.main import app
from app.routers.catalog_objects import get_simbad_limiter
from app.services import simbad as sb
from app.services.catalog_objects import FALLBACK_TEXT
from app.services.rate_limit import SlidingWindowLimiter, TokenBucket
from app.services.simbad import SimbadRow, SimbadService, SimbadUnavailable, get_simbad_service
from tests.test_simbad_service import BARNARD, FakeTransport, tap

URL = "/api/v1/catalog-objects"


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("a test tried to reach the network")
    monkeypatch.setattr(sb.httpx, "post", forbidden)
    yield
    app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# The bucket itself
# --------------------------------------------------------------------------- #


def test_burst_is_accepted_then_the_next_call_is_limited():
    clock = Clock()
    bucket = TokenBucket(10, 3.0, clock=clock)
    assert [bucket.check() for _ in range(10)] == [None] * 10
    wait = bucket.check()
    assert wait == pytest.approx(1 / 3)  # one token comes back every 1/3 s


def test_bucket_refills_with_time_and_never_beyond_the_burst():
    clock = Clock()
    bucket = TokenBucket(10, 3.0, clock=clock)
    for _ in range(10):
        bucket.check()
    clock.t += 1.0
    assert bucket.tokens == pytest.approx(3.0)
    assert [bucket.check() for _ in range(3)] == [None] * 3
    assert bucket.check() is not None
    clock.t += 3600
    assert bucket.tokens == pytest.approx(10.0)  # capped at the burst


def test_partial_refill_and_zero_rate():
    clock = Clock()
    bucket = TokenBucket(1, 2.0, clock=clock)
    assert bucket.check() is None
    clock.t += 0.25  # half a token
    assert bucket.check() == pytest.approx(0.25)
    clock.t += 0.25
    assert bucket.check() is None
    assert math.isinf(TokenBucket(0, 0.0).check())
    with pytest.raises(ValueError):
        TokenBucket(-1, 1.0)


def test_settings_from_environment(monkeypatch):
    for v in ("SIMBAD_BURST", "SIMBAD_RATE_PER_S", "SIMBAD_GLOBAL_QPS"):
        monkeypatch.delenv(v, raising=False)
    cfg = SimbadSettings()
    assert (cfg.burst, cfg.rate_per_s) == (10, 3.0)
    monkeypatch.setenv("SIMBAD_GLOBAL_QPS", "2")  # old name still works for the rate
    assert SimbadSettings().rate_per_s == 2.0
    monkeypatch.setenv("SIMBAD_BURST", "4")
    monkeypatch.setenv("SIMBAD_RATE_PER_S", "1.5")  # new name wins
    cfg = SimbadSettings()
    assert (cfg.burst, cfg.rate_per_s) == (4, 1.5)
    service = SimbadService(cfg, transport=FakeTransport(tap()))
    assert isinstance(service.gate, TokenBucket) and (service.gate.burst, service.gate.rate_per_s) == (4, 1.5)


# --------------------------------------------------------------------------- #
# The service while the bucket is empty: saved data first, never an error
# --------------------------------------------------------------------------- #


def paced_service(transport, clock, burst=10, rate=3.0, precomputed=None):
    return SimbadService(SimbadSettings(tap_url="https://tap.example/sync"), transport=transport,
                         gate=TokenBucket(burst, rate, clock=clock), precomputed=precomputed)


def test_a_burst_of_new_fields_goes_to_simbad_then_saved_data_is_served():
    clock = Clock()
    t = FakeTransport(tap(BARNARD))
    pre = [SimbadRow("Pre", "G", "Galaxy", 1.0, 1.0, None, None, 1, None, None)]
    s = paced_service(t, clock, precomputed=lambda key: pre if key == "BARNARDS_STAR" else None)
    for i in range(10):
        assert s.lookup(10.0 + i, 0.0, 0.05, "all").source == "simbad"
    assert len(t.calls) == 10
    # 11th new field in the same instant: no request; Golden Path data if there is some, else empty
    gold = s.lookup(50.0, 0.0, 0.05, "all", golden_key="BARNARDS_STAR")
    assert (gold.source, gold.fallback_reason, [r.main_id for r in gold.rows]) == ("precomputed", "simbad_rate_limited", ["Pre"])
    none = s.lookup(51.0, 0.0, 0.05, "all")
    assert (none.source, none.fallback_reason, none.rows) == ("none", "simbad_rate_limited", [])
    assert len(t.calls) == 10
    # a field already asked is served from the cache without spending a token
    assert s.lookup(10.0, 0.0, 0.05, "all").source == "cache"
    # one second later three tokens are back
    clock.t += 1.0
    assert [s.lookup(60.0 + i, 0.0, 0.05, "all").source for i in range(4)] == ["simbad"] * 3 + ["none"]


def test_stale_cache_is_preferred_over_precomputed_when_the_bucket_is_empty():
    clock = Clock()
    cache = sb.TTLCache(60, 10, clock=clock)
    pre = [SimbadRow("Pre", "*", "Star", 1.0, 1.0, None, None, 1, None, None)]
    s = SimbadService(SimbadSettings(tap_url="u"), transport=FakeTransport(tap(BARNARD)), cache=cache,
                      gate=TokenBucket(1, 0.001, clock=clock), precomputed=lambda key: pre)
    assert s.lookup(1.0, 1.0, 0.05, "all", golden_key="BARNARDS_STAR").source == "simbad"
    clock.t += 120  # cache entry expired; still almost no token (0.12)
    out = s.lookup(1.0, 1.0, 0.05, "all", golden_key="BARNARDS_STAR")
    assert (out.source, out.fallback_reason, out.rows[0].main_id) == ("cache", "simbad_rate_limited", "* 699")


# --------------------------------------------------------------------------- #
# What the user reads
# --------------------------------------------------------------------------- #


def test_texts_tell_our_pacing_apart_from_a_simbad_failure():
    for src in ("cache", "precomputed", "none"):
        ours, theirs = FALLBACK_TEXT[("simbad_rate_limited", src)], FALLBACK_TEXT[("simbad_unavailable", src)]
        assert ours.startswith("We are slowing down our queries to SIMBAD")
        assert "too many requests" not in ours.lower() and "SIMBAD is" not in ours  # SIMBAD is not blamed
        assert theirs.startswith("SIMBAD did not answer in time")
    assert "last result saved" in FALLBACK_TEXT[("simbad_rate_limited", "cache")]
    assert "precomputed list" in FALLBACK_TEXT[("simbad_rate_limited", "precomputed")]
    assert "Try again in a few seconds" in FALLBACK_TEXT[("simbad_rate_limited", "none")]


def serve(service):
    app.dependency_overrides[get_simbad_service] = lambda: service
    app.dependency_overrides[get_simbad_limiter] = lambda: SlidingWindowLimiter(10_000, 60)
    return TestClient(app)


def test_endpoint_messages_in_each_case():
    clock = Clock()
    pre = {"BARNARDS_STAR": [SimbadRow("Pre", "*", "Star", 269.446, 4.767, None, None, 1, None, None)]}
    c = serve(paced_service(FakeTransport(tap()), clock, burst=1, rate=0.5, precomputed=pre.get))
    first = c.get(URL, params={"target": "WISE_0855"}).json()
    assert (first["source"], first["fallback_reason"], first["fallback_message"]) == ("simbad", None, None)
    gold = c.get(URL, params={"target": "BARNARDS_STAR"})
    assert gold.status_code == 200  # an empty bucket is never an error
    body = gold.json()
    assert (body["source"], body["fallback_reason"]) == ("precomputed", "simbad_rate_limited")
    assert body["fallback_message"] == FALLBACK_TEXT[("simbad_rate_limited", "precomputed")]
    assert len(body["objects"]) == 1
    body = c.get(URL, params={"ra": 10, "dec": 10}).json()
    assert (body["source"], body["fallback_reason"], body["objects"]) == ("none", "simbad_rate_limited", [])
    assert body["fallback_message"] == FALLBACK_TEXT[("simbad_rate_limited", "none")]
    # SIMBAD itself failing keeps its own wording
    clock.t += 10
    c = serve(paced_service(FakeTransport(error=SimbadUnavailable("down")), clock, burst=5, precomputed=pre.get))
    body = c.get(URL, params={"ra": 20, "dec": 20}).json()
    assert body["fallback_reason"] == "simbad_unavailable"
    assert body["fallback_message"] == FALLBACK_TEXT[("simbad_unavailable", "none")]


def test_per_ip_429_still_carries_retry_after():
    c = serve(paced_service(FakeTransport(tap()), Clock()))
    app.dependency_overrides[get_simbad_limiter] = lambda: SlidingWindowLimiter(0, 60)
    res = c.get(URL, params={"target": "BARNARDS_STAR"})
    assert res.status_code == 429 and int(res.headers["Retry-After"]) >= 1
