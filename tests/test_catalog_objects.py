"""GET /api/v1/catalog-objects: projection, proper motion, fallbacks, edge cases. SIMBAD is always faked."""

import math
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.credits import SIMBAD_CREDIT
from app.core.sky_projection import propagate, years_since_j2000
from app.core.targets import CATALOG
from app.main import app
from app.routers.catalog_objects import get_simbad_limiter
from app.services.catalog_objects import ImageGeometry, image_geometry, place
from app.services.game import J2000, position_at, world_to_png
from app.services.rate_limit import SlidingWindowLimiter
from app.services.simbad import SimbadRow, SimbadService, SimbadUnavailable, get_simbad_service
from app.core.simbad_settings import SimbadSettings
from tests.conftest import load_raw
from tests.test_game import centroid
from tests.test_simbad_service import COLS, FakeTransport, tap

URL = "/api/v1/catalog-objects"
README = (Path(__file__).resolve().parents[1] / "README.md").read_text("utf-8")
STATIC = Path(__file__).resolve().parents[1] / "app"


def j2000_row(target_key: str, **over) -> list:
    """The SIMBAD-style row (J2000 position + proper motion) of a repo target, derived from ITS Gaia/WISE
    reference data with the game's own model. It is not a SIMBAD answer: it makes the test check the
    propagation, the projection and the axis conventions, not SIMBAD's numbers."""
    t = CATALOG[target_key]
    ra, dec = position_at(t, J2000)
    row = {"main_id": t.name, "otype": "BD*" if "WISE" in t.name else "Flare*", "otype_label": "Star",
           "ra": ra, "dec": dec, "pmra": t.pm_ra_cosdec_mas_yr, "pmdec": t.pm_dec_mas_yr, "nbref": 100,
           "V": 10.0, "G": None, "R": None, "J": None, "H": None, "K": None}
    row.update(over)
    return [row[c] for c in COLS]


def make_client(transport, **svc):
    service = SimbadService(SimbadSettings(tap_url="https://tap.example/sync"), transport=transport,
                            gate=SlidingWindowLimiter(1000, 1.0), **svc)
    app.dependency_overrides[get_simbad_service] = lambda: service
    app.dependency_overrides[get_simbad_limiter] = lambda: SlidingWindowLimiter(10_000, 60)
    return TestClient(app), service


@pytest.fixture(autouse=True)
def _clean():
    yield
    app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# Barnard's Star: the proper-motion test
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("epoch_id", ["2025_M0", "2025_M7", "2025_M12"])
def test_barnard_marker_matches_the_game_position_and_the_light_in_the_image(epoch_id):
    t = CATALOG["BARNARDS_STAR"]
    c, _ = make_client(FakeTransport(tap(j2000_row("BARNARDS_STAR"))))
    body = c.get(URL, params={"target": "BARNARDS_STAR", "epoch": epoch_id}).json()
    (obj,) = body["objects"]
    assert obj["proper_motion_applied"] is True
    # 1) the game's ground truth, same criterion as tests/test_game.py: within 1.5 px (0.75 arcsec)
    raw = load_raw("barnards_star.json")
    date = next(e["date"] for e in raw["timelapse_epochs"] if e["epoch_id"] == epoch_id)
    when = datetime.fromisoformat(date.replace("Z", "+00:00"))
    expected = world_to_png(raw["patch"]["wcs"], 600, *position_at(t, when))
    assert math.dist((obj["x"], obj["y"]), expected) < 1.5
    # 2) the real image: the light centroid of the star in the PNG that the user sees
    idx = {"2025_M0": 1, "2025_M7": 2, "2025_M12": 3}[epoch_id]
    png = STATIC / "static" / "golden_path" / f"barnardsstar_epoch{idx}_rgb.png"
    assert math.dist((obj["x"], obj["y"]), centroid(png, obj["x"], obj["y"])) < 1.5
    assert body["epoch_applied"]["epoch_id"] == epoch_id and body["epoch_applied"]["date"] == date


def test_without_proper_motion_barnard_is_far_from_where_it_is():
    """Same star, same image, no propagation: the marker would be 231 px below the image (dropped)."""
    geo = image_geometry("BARNARDS_STAR", None, None, 5.0, "2025_M0")
    t = CATALOG["BARNARDS_STAR"]
    ra, dec = position_at(t, J2000)
    row = SimbadRow("Barnard", "Flare*", "Flare Star", ra, dec, None, None, 1, None, None)
    assert place(row, geo) is None  # outside the 600 x 600 image
    x, y = world_to_png(geo.wcs, 600, ra, dec)
    assert math.dist((x, y), (300.0, 300.0)) > 200  # the star is at the centre at this epoch: >100x the 1.5 px criterion
    row_pm = SimbadRow("Barnard", "Flare*", "Flare Star", ra, dec, t.pm_ra_cosdec_mas_yr, t.pm_dec_mas_yr, 1, None, None)
    assert math.dist((place(row_pm, geo).x, place(row_pm, geo).y), (300, 300)) < 1.5


def test_epsilon_indi_b_also_lands_on_its_light():
    t = CATALOG["EPSILON_INDI_B"]
    c, _ = make_client(FakeTransport(tap(j2000_row("EPSILON_INDI_B"))))
    for idx, epoch_id in enumerate(("2025_M0", "2025_M5", "2025_M12", "2025_M17"), start=1):
        (obj,) = c.get(URL, params={"target": "EPSILON_INDI_B", "epoch": epoch_id}).json()["objects"]
        png = STATIC / "static" / "golden_path" / f"epsilonindib_epoch{idx}_rgb.png"
        assert math.dist((obj["x"], obj["y"]), centroid(png, obj["x"], obj["y"])) < 1.5, epoch_id


def test_the_query_reaches_the_star_that_moved_out_of_the_plain_cone():
    t = FakeTransport(tap(j2000_row("BARNARDS_STAR")))
    c, _ = make_client(t)
    c.get(URL, params={"target": "BARNARDS_STAR"})
    adql = t.calls[0][1]
    assert "CONTAINS(POINT('ICRS', b.ra, b.dec), CIRCLE('ICRS'" in adql and "OR DISTANCE(" in adql
    raw = load_raw("barnards_star.json")["patch"]["wcs"]
    assert f"{raw['crval1']:.3f}" in adql  # centred on the image centre (rounded)


# --------------------------------------------------------------------------- #
# Response contract
# --------------------------------------------------------------------------- #


def test_response_shape_ranking_truncation_and_filters():
    stars = [["S%d" % i, "*" if i % 2 else "BD*", "Star", 269.446 + 0.001 * i, 4.767, None, None, i, 10.0 + i,
              None, None, None, None, None] for i in range(1, 7)]
    c, _ = make_client(FakeTransport(tap(*stars)))
    body = c.get(URL, params={"target": "BARNARDS_STAR", "max_n": 3}).json()
    assert [o["id"] for o in body["objects"]] == ["S6", "S5", "S4"]  # most references first
    assert body["truncated"] is True and body["source"] == "simbad" and body["fallback_reason"] is None
    assert body["image_size"] == {"width": 600, "height": 600}
    assert set(body["objects"][0]) == {"id", "otype", "otype_label", "x", "y", "mag", "mag_band", "nbref",
                                      "simbad_url", "proper_motion_applied"}
    assert body["objects"][0]["proper_motion_applied"] is False
    assert body["objects"][0]["simbad_url"] == "https://simbad.cds.unistra.fr/simbad/sim-id?Ident=S6"
    assert body["objects"][0]["mag"] == 16.0 and body["objects"][0]["mag_band"] == "V"
    assert "SPHEREx detections" in body["notice"]
    ids = [d["id"] for d in body["data_credits"]]
    assert ids[0] == "simbad" and body["data_credits"][0] == SIMBAD_CREDIT
    assert not c.get(URL, params={"target": "BARNARDS_STAR", "max_n": 6}).json()["truncated"]


def test_objects_outside_the_image_are_dropped():
    inside = ["IN", "*", "Star", 269.446, 4.767, None, None, 5, None, None, None, None, None, None]
    outside = ["OUT", "*", "Star", 269.446, 4.767 + 0.05, None, None, 9, None, None, None, None, None, None]  # 3' north
    c, _ = make_client(FakeTransport(tap(inside, outside)))
    body = c.get(URL, params={"target": "BARNARDS_STAR", "max_n": 50}).json()
    assert [o["id"] for o in body["objects"]] == ["IN"] and body["truncated"] is False
    (o,) = body["objects"]
    assert 0 <= o["x"] < 600 and 0 <= o["y"] < 600


def test_empty_field_is_a_200_with_an_empty_list():
    c, _ = make_client(FakeTransport(tap()))
    res = c.get(URL, params={"target": "WISE_0855"})
    body = res.json()
    assert res.status_code == 200
    assert (body["objects"], body["truncated"], body["source"], body["fallback_reason"]) == ([], False, "simbad", None)


def test_validation():
    c, _ = make_client(FakeTransport(tap()))
    assert c.get(URL, params={"target": "BARNARDS_STAR", "max_n": 51}).status_code == 422
    assert c.get(URL, params={"target": "BARNARDS_STAR", "max_n": 0}).status_code == 422
    assert c.get(URL, params={"target": "BARNARDS_STAR", "type_filter": "by_brightness"}).status_code == 422
    assert c.get(URL).status_code == 422
    assert c.get(URL, params={"target": "NOPE"}).status_code == 404
    assert c.get(URL, params={"target": "BARNARDS_STAR", "epoch": "2099_M1"}).status_code == 422
    assert c.get(URL, params={"ra": 10, "dec": 10, "fov": 20}).status_code == 422
    assert c.get(URL, params={"ra": 10, "dec": 10, "epoch": "2025_M0"}).status_code == 422
    for tf in ("all", "no_common_stars", "ir_galaxies"):
        assert c.get(URL, params={"target": "BARNARDS_STAR", "type_filter": tf}).status_code == 200


def test_ra_dec_inside_a_golden_target_reuses_its_cached_image():
    t = CATALOG["BARNARDS_STAR"]
    c, _ = make_client(FakeTransport(tap(j2000_row("BARNARDS_STAR"))))
    body = c.get(URL, params={"ra": t.ra_deg, "dec": t.dec_deg}).json()
    assert body["epoch_applied"]["epoch_id"] == "2025_M0" and len(body["objects"]) == 1


# --------------------------------------------------------------------------- #
# Reliability
# --------------------------------------------------------------------------- #


def test_simbad_failure_serves_cache_then_precomputed_then_empty_with_english_messages():
    t = FakeTransport(tap(j2000_row("BARNARDS_STAR")))
    c, svc = make_client(t, precomputed=lambda key: [SimbadRow.from_dict({
        "main_id": "Pre", "otype": "*", "otype_label": "Star", "ra": 269.446, "dec": 4.767, "nbref": 1}
        )] if key == "BARNARDS_STAR" else None)
    assert c.get(URL, params={"target": "BARNARDS_STAR"}).json()["source"] == "simbad"
    t.error = SimbadUnavailable("down")
    svc.cache._clock = lambda: 1e9  # every entry is past its TTL, but a stale entry is still a valid fallback
    body = c.get(URL, params={"target": "BARNARDS_STAR"}).json()
    assert (body["source"], body["fallback_reason"], len(body["objects"])) == ("cache", "simbad_unavailable", 1)
    assert body["fallback_message"] == "SIMBAD did not answer in time. Showing the last result saved for this field."
    body = c.get(URL, params={"target": "BARNARDS_STAR", "type_filter": "no_common_stars"}).json()
    assert (body["source"], body["objects"]) == ("precomputed", [])  # the only saved row is a plain star
    body = c.get(URL, params={"target": "BARNARDS_STAR", "epoch": "2025_M0"}).json()
    assert body["source"] == "cache"  # same key: same centre, radius, filter and quarter-year
    body = c.get(URL, params={"target": "BARNARDS_STAR", "epoch": "2025_M7"}).json()
    assert body["source"] == "precomputed"  # another visit is 0.5 yr later: another widening, another key
    body = c.get(URL, params={"target": "WISE_0855"}).json()
    assert (body["source"], body["objects"], body["fallback_reason"]) == ("none", [], "simbad_unavailable")
    assert "no saved result" in body["fallback_message"]


def test_global_cap_toward_simbad_reports_rate_limited():
    t = FakeTransport(tap())
    service = SimbadService(SimbadSettings(tap_url="u"), transport=t, gate=SlidingWindowLimiter(1, 1.0))
    app.dependency_overrides[get_simbad_service] = lambda: service
    app.dependency_overrides[get_simbad_limiter] = lambda: SlidingWindowLimiter(10_000, 60)
    c = TestClient(app)
    assert c.get(URL, params={"target": "BARNARDS_STAR"}).json()["source"] == "simbad"
    body = c.get(URL, params={"target": "WISE_0855"}).json()
    assert (body["fallback_reason"], body["source"]) == ("simbad_rate_limited", "none")
    assert body["fallback_message"].startswith("SIMBAD is receiving too many requests")
    assert len(t.calls) == 1


def test_per_ip_rate_limit_is_a_429_with_retry_after():
    c, _ = make_client(FakeTransport(tap()))
    app.dependency_overrides[get_simbad_limiter] = lambda: limiter
    limiter = SlidingWindowLimiter(2, 60)
    assert [c.get(URL, params={"target": "BARNARDS_STAR"}).status_code for _ in range(3)] == [200, 200, 429]
    res = c.get(URL, params={"target": "BARNARDS_STAR"})
    assert res.json()["code"] == "http_429" and int(res.headers["Retry-After"]) >= 1


# --------------------------------------------------------------------------- #
# Edge cases: poles, RA = 0/360, empty field
# --------------------------------------------------------------------------- #


def test_near_the_north_pole():
    ra, dec = 40.0, 89.98
    obj = ["POLE", "*", "Star", 40.0, 89.98 + 1 / 60.0, None, None, 3, None, None, None, None, None, None]
    c, _ = make_client(FakeTransport(tap(obj)))
    body = c.get(URL, params={"ra": ra, "dec": dec, "fov": 5}).json()
    (o,) = body["objects"]
    assert o["x"] == pytest.approx(300.0, abs=0.5) and o["y"] == pytest.approx(180.0, abs=0.5)  # 1' north = 120 px up


def test_proper_motion_across_the_pole_does_not_break():
    # 100 arcsec/yr, 25 years: a ~40' move across a field 1 arcmin from the pole; must stay finite and in range
    ra, dec = propagate(10.0, 89.99, 100_000.0, 0.0, 25.0)
    assert 0 <= ra < 360 and 89.0 < dec <= 90.0


def test_ra_zero_crossing():
    west = ["W", "*", "Star", 359.9995, 10.0, None, None, 2, None, None, None, None, None, None]
    east = ["E", "*", "Star", 0.0015, 10.0, None, None, 1, None, None, None, None, None, None]
    t = FakeTransport(tap(west, east))
    c, _ = make_client(t)
    body = c.get(URL, params={"ra": 0.0005, "dec": 10.0, "fov": 5}).json()
    by_id = {o["id"]: o for o in body["objects"]}
    assert set(by_id) == {"W", "E"}
    assert by_id["W"]["x"] > by_id["E"]["x"]  # East is left: RA 0.0015 is east of RA 359.9995
    assert by_id["W"]["x"] - 300 == pytest.approx(0.001 * math.cos(math.radians(10)) * 7200, abs=0.6)
    assert "CIRCLE('ICRS', 0.001000, 10.000000" in t.calls[0][1]  # centre rounded, RA kept in [0, 360)


def test_ra_just_below_360_is_wrapped_in_the_query():
    t = FakeTransport(tap())
    c, _ = make_client(t)
    c.get(URL, params={"ra": 359.9996, "dec": 10.0})
    assert "CIRCLE('ICRS', 0.000000, 10.000000" in t.calls[0][1] or "CIRCLE('ICRS', 360" not in t.calls[0][1]


def test_synthetic_geometry_for_coordinates_outside_the_golden_path():
    when = datetime(2026, 1, 1, tzinfo=UTC)
    geo = image_geometry(None, 180.0, 0.0, 5.0, None, now=when)
    assert (geo.width, geo.height, geo.epoch_id, geo.when) == (600, 600, None, when)
    assert geo.radius_deg == pytest.approx(5 * math.sqrt(2) / 2 / 60)
    assert years_since_j2000(when) == pytest.approx(26.0, abs=0.01)


# --------------------------------------------------------------------------- #
# Documentation stays in step with the code
# --------------------------------------------------------------------------- #


def test_simbad_credit_is_written_the_same_way_in_the_readme():
    data_used = README.split("## Data used", 1)[1].split("\n## ", 1)[0]
    for key in ("doi", "url", "citation", "access", "used_for"):
        assert SIMBAD_CREDIT[key] in data_used, key
