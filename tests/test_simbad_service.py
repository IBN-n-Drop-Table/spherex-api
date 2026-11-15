"""SIMBAD client with a fake transport: no test here touches the network."""

import json

import httpx
import pytest

from app.core.simbad_settings import SimbadSettings
from app.services import simbad as sb
from app.services.rate_limit import SlidingWindowLimiter
from app.services.simbad import (
    SimbadRow, SimbadService, SimbadUnavailable, TTLCache, build_adql, cache_key, parse_tap_json,
)
from app.services.simbad_precomputed import load_precomputed, precomputed_path

COLS = ["main_id", "otype", "otype_label", "ra", "dec", "pmra", "pmdec", "nbref", "V", "G", "R", "J", "H", "K"]


def tap(*records):
    """A TAP JSON answer shaped like the ones the CDS TAP service returns (layout unverified, see simbad.py)."""
    return {"metadata": [{"name": c} for c in COLS], "data": [list(r) for r in records]}


BARNARD = ["* 699", "Flare*", "Flare Star", 269.45402, 4.66828, -802.803, 10362.542, 1500, 9.51, 8.1, None, 5.2, 4.8, 4.52]
BD = ["WISE J085510.83-071442.5", "BD*", "Brown Dwarf", 133.79513, -7.24515, -8118.0, 673.0, 150, None, None, None, None, None, 17.3]
STAR = ["HD 1", "*", "Star", 1.0, 1.0, None, None, 3, 10.0, None, None, None, None, None]
GAL = ["NGC 1", "G", "Galaxy", 2.0, 2.0, None, None, 90, 13.0, None, None, None, None, None]


class FakeTransport:
    def __init__(self, answer=None, error=None):
        self.answer, self.error, self.calls = answer, error, []

    def __call__(self, url, adql, timeout):
        self.calls.append((url, adql, timeout))
        if self.error:
            raise self.error
        return self.answer


def service(transport, **kw):
    cfg = SimbadSettings(tap_url="https://tap.example/sync")
    return SimbadService(cfg, transport=transport, gate=SlidingWindowLimiter(1000, 1.0), **kw)


def test_adql_uses_geometric_containment_and_the_confirmed_columns():
    q = build_adql(269.454, 4.668, 0.0589, "all", 200)
    assert "CONTAINS(POINT('ICRS', b.ra, b.dec), CIRCLE('ICRS', 269.454000, 4.668000, 0.058900)) = 1" in q
    assert q.startswith("SELECT TOP 200 b.main_id, b.otype, o.description AS otype_label, b.ra, b.dec, b.pmra, b.pmdec, b.nbref")
    assert "FROM basic AS b" in q and "allfluxes AS f ON b.oid = f.oidref" in q
    assert q.endswith("ORDER BY nbref DESC")
    assert "NOT IN" not in q
    assert "b.otype NOT IN" in build_adql(1, 1, 0.1, "no_common_stars", 5)
    assert "b.otype IN ('G'" in build_adql(1, 1, 0.1, "ir_galaxies", 5)


def test_parse_rows():
    rows = parse_tap_json(tap(BARNARD, BD))
    b, bd = rows
    assert (b.main_id, b.otype, b.mag, b.mag_band, b.pmra, b.pmdec, b.nbref) == ("* 699", "Flare*", 9.51, "V", -802.803, 10362.542, 1500)
    assert (bd.mag, bd.mag_band) == (17.3, "K")  # no V/G/R/J/H: falls to the first band that exists
    assert rows[0].otype_label == "Flare Star"


def test_parse_handles_missing_data_and_padded_ids():
    padded = ["V*   RW    Aur", "TT*", None, 76.95, 30.40, None, None, None, None, None, None, None, None, None]
    no_position = ["X", "*", "Star", None, None, None, None, 1, None, None, None, None, None, None]
    rows = parse_tap_json(tap(padded, no_position))
    assert len(rows) == 1
    r = rows[0]
    assert (r.main_id, r.otype_label, r.pmra, r.pmdec, r.nbref, r.mag, r.mag_band) == ("V* RW Aur", "TT*", None, None, 0, None, None)


def test_parse_rejects_things_that_are_not_a_table():
    for bad in (None, [], {"error": "x"}, {"metadata": 1, "data": []}):
        with pytest.raises(SimbadUnavailable):
            parse_tap_json(bad)
    assert parse_tap_json(tap()) == []  # an empty field is a valid answer


def test_http_transport_maps_every_failure_to_unavailable(monkeypatch):
    def timeout(*a, **k):
        raise httpx.ReadTimeout("slow")

    monkeypatch.setattr(sb.httpx, "post", timeout)
    with pytest.raises(SimbadUnavailable):
        sb.http_transport("https://x/sync", "SELECT 1", 5.0)

    class Resp:
        def __init__(self, status, body):
            self.status_code, self.body = status, body

        def raise_for_status(self):
            if self.status_code >= 400:
                raise httpx.HTTPStatusError("e", request=None, response=None)

        def json(self):
            return json.loads(self.body)

    for status, body in ((503, "{}"), (200, "<VOTABLE/>")):
        monkeypatch.setattr(sb.httpx, "post", lambda *a, _s=status, _b=body, **k: Resp(_s, _b))
        with pytest.raises(SimbadUnavailable):
            sb.http_transport("https://x/sync", "SELECT 1", 5.0)

    seen = {}

    def ok(url, **kw):
        seen.update(kw)
        return Resp(200, '{"metadata": [], "data": []}')

    monkeypatch.setattr(sb.httpx, "post", ok)
    assert sb.http_transport("https://x/sync", "SELECT 1", 5.0) == {"metadata": [], "data": []}
    assert seen["data"]["FORMAT"] == "json" and seen["data"]["LANG"] == "ADQL" and seen["data"]["QUERY"] == "SELECT 1"
    assert seen["timeout"].read == 5.0


def test_live_query_then_cache_hit_without_a_second_call():
    t = FakeTransport(tap(BARNARD))
    s = service(t)
    first = s.lookup(269.4540, 4.6683, 0.0589, "all")
    assert (first.source, first.fallback_reason, len(first.rows)) == ("simbad", None, 1)
    second = s.lookup(269.4541, 4.6684, 0.0589, "all")  # rounds to the same key
    assert (second.source, second.fallback_reason) == ("cache", None)
    assert len(t.calls) == 1
    assert t.calls[0][0] == "https://tap.example/sync" and t.calls[0][2] == 5.0
    s.lookup(269.4540, 4.6683, 0.0589, "ir_galaxies")  # another filter is another key
    assert len(t.calls) == 2


def test_the_query_is_made_at_the_rounded_centre_with_padding():
    t = FakeTransport(tap())
    service(t).lookup(269.45449, 4.66851, 0.0589, "all")
    assert "CIRCLE('ICRS', 269.454000, 4.669000, 0.060500)" in t.calls[0][1]  # radius rounded to 0.059, plus 0.0015 of padding


def test_cache_key_rounding_and_wrap():
    assert cache_key(359.99996, 1.0, 0.05, "all") == (0.0, 1.0, 0.05, "all", 0.0)
    assert cache_key(10.0004, -5.0004, 0.05, "all") == cache_key(10.0001, -5.0001, 0.05, "all")
    assert cache_key(10.0, 5.0, 0.05, "all") != cache_key(10.0, 5.0, 0.05, "ir_galaxies")


def test_ttl_expiry_triggers_a_new_query_and_stale_data_survives_an_outage():
    now = [0.0]
    cache = TTLCache(60, 10, clock=lambda: now[0])
    t = FakeTransport(tap(BARNARD))
    s = service(t, cache=cache)
    s.lookup(1.0, 1.0, 0.05, "all")
    now[0] = 61.0
    t.answer = tap(BARNARD, BD)
    assert len(s.lookup(1.0, 1.0, 0.05, "all").rows) == 2 and len(t.calls) == 2  # expired: refreshed
    now[0] = 200.0
    t.error = SimbadUnavailable("down")
    out = s.lookup(1.0, 1.0, 0.05, "all")
    assert (out.source, out.fallback_reason, len(out.rows)) == ("cache", "simbad_unavailable", 2)


def test_cache_is_bounded():
    c = TTLCache(60, 2)
    for i in range(5):
        c.put((i,), [])
    assert c.get((0,)) is None and c.get((4,)) == []


def test_global_rate_cap_never_sends_the_request():
    t = FakeTransport(tap(BARNARD))
    s = SimbadService(SimbadSettings(tap_url="u"), transport=t, gate=SlidingWindowLimiter(2, 1.0))
    for i in range(2):
        assert s.lookup(10.0 + i, 0.0, 0.05, "all").source == "simbad"
    out = s.lookup(50.0, 0.0, 0.05, "all")
    assert (out.source, out.fallback_reason, out.rows) == ("none", "simbad_rate_limited", [])
    assert len(t.calls) == 2


def test_precomputed_fallback_is_filtered_and_empty_when_there_is_none():
    rows = [SimbadRow.from_dict(dict(zip(COLS[:8] + ["mag", "mag_band"], r[:8] + [None, None], strict=False)))
            for r in (BARNARD, STAR, GAL)]
    s = service(FakeTransport(error=SimbadUnavailable("down")),
                precomputed=lambda key: rows if key == "BARNARDS_STAR" else None)
    out = s.lookup(1.0, 1.0, 0.05, "no_common_stars", golden_key="BARNARDS_STAR")
    assert (out.source, out.fallback_reason) == ("precomputed", "simbad_unavailable")
    assert [r.main_id for r in out.rows] == ["* 699", "NGC 1"]  # the plain star is filtered out in Python
    out = s.lookup(1.0, 1.0, 0.05, "ir_galaxies", golden_key="BARNARDS_STAR")
    assert [r.main_id for r in out.rows] == ["NGC 1"]
    none = s.lookup(2.0, 2.0, 0.05, "all", golden_key="WISE_0855")
    assert (none.source, none.rows, none.fallback_reason) == ("none", [], "simbad_unavailable")


def test_precomputed_file_loading(tmp_path):
    assert load_precomputed("BARNARDS_STAR", tmp_path) is None
    path = precomputed_path("BARNARDS_STAR", tmp_path)
    assert path.parent.name == "simbad" and path.name == "barnards_star.json"
    path.parent.mkdir()
    row = {"main_id": "* 699", "otype": "Flare*", "otype_label": "Flare Star", "ra": 269.45, "dec": 4.67,
           "pmra": -802.8, "pmdec": 10362.5, "nbref": 1500, "mag": 9.5, "mag_band": "V"}
    path.write_text(json.dumps({"objects": [row]}), "utf-8")
    assert load_precomputed("BARNARDS_STAR", tmp_path)[0].main_id == "* 699"
    path.write_text("{not json", "utf-8")
    assert load_precomputed("BARNARDS_STAR", tmp_path) is None


def test_cone_is_widened_for_fast_movers_and_kept_exact_for_each_row():
    plain = build_adql(269.446, 4.767, 0.0589, "all", 200)
    assert "DISTANCE" not in plain and "CIRCLE('ICRS', 269.446000, 4.767000, 0.058900)" in plain
    q = build_adql(269.446, 4.767, 0.0589, "all", 200, years=25.75)
    wide = 0.0589 + 11000 / 3.6e6 * 25.75
    assert f"CIRCLE('ICRS', 269.446000, 4.767000, {wide:.6f})) = 1" in q  # ~0.138 deg: Barnard's J2000 spot is in it
    assert "DISTANCE(POINT('ICRS', b.ra, b.dec), POINT('ICRS', 269.446000, 4.767000)) <= 0.058900 + " in q
    assert "SQRT(POWER(b.pmra, 2) + POWER(b.pmdec, 2)) / 3600000.0 * 25.7500" in q
    assert q.index("CONTAINS") < q.index("OR DISTANCE") and q.endswith("ORDER BY nbref DESC")


def test_years_are_rounded_up_to_a_quarter_and_are_part_of_the_cache_key():
    from app.services.simbad import quantize_years
    assert quantize_years(25.61) == 25.75 and quantize_years(-1) == 0.0 and quantize_years(25.5) == 25.5
    assert cache_key(1, 1, 0.05, "all", 25.6) == cache_key(1, 1, 0.05, "all", 25.7)
    assert cache_key(1, 1, 0.05, "all", 25.6) != cache_key(1, 1, 0.05, "all", 26.4)
