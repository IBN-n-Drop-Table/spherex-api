"""Game "Encuentra lo que se mueve": ground truth, crops, sessions, scoring, leaderboard."""

import math
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.core.patch_geometry import png_to_world
from app.core.wow_settings import GameSettings
from app.main import app
from app.routers.game import get_game_limiters
from app.services.game import (
    HIT_RADIUS_PX,
    CropStore,
    GameService,
    distance_to_segment,
    game_pairs,
    get_game_service,
    round_score,
    world_to_png,
)
from app.services.game_scores import (
    InMemoryScoreRepository,
    ScoreService,
    build_score_service,
    get_score_service,
)
from app.services.rate_limit import SlidingWindowLimiter

BASE = "/api/v1/game"


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def centroid(path: Path, x: float, y: float, r: int = 14) -> tuple[float, float]:
    """Light centroid (above the image median) in a (2r+1)^2 box around continuous (x, y)."""
    with Image.open(path) as img:
        gray = img.convert("L")
        values = sorted(gray.tobytes())  # mode L: one byte per pixel
        median = values[len(values) // 2]
        xi, yi = int(x), int(y)
        box = gray.crop((xi - r, yi - r, xi + r + 1, yi + r + 1))
        w = box.width
        sx = sy = s = 0.0
        for i, v in enumerate(box.tobytes()):
            v = max(v - median, 0)
            sx += v * (xi - r + i % w + 0.5)
            sy += v * (yi - r + i // w + 0.5)
            s += v
    return sx / s, sy / s


# --------------------------------------------------------------------------- #
# Ground truth and geometry
# --------------------------------------------------------------------------- #


def test_only_the_two_measurable_movers_are_used():
    pairs = game_pairs()
    assert {p.target.key for p in pairs} == {"BARNARDS_STAR", "EPSILON_INDI_B"}
    assert len(pairs) == 7  # Barnard: 2 consecutive + first-vs-last; Eps Indi B: 3 + 1
    by_diff = {d: sorted(p.pair_id for p in pairs if p.difficulty == d) for d in ("easy", "medium", "hard")}
    assert by_diff["easy"] == ["BARNARDS_STAR:2025_M0:2025_M12"]
    assert len(by_diff["medium"]) == 3 and len(by_diff["hard"]) == 3
    assert all(p.weak_signal for p in pairs if p.difficulty == "hard")


def test_predicted_positions_match_the_star_in_the_images():
    """Proper motion + WCS vs the light centroid measured in each cached PNG."""
    checked = set()
    for p in game_pairs():
        for img, pos in ((p.image_a, p.pos_a), (p.image_b, p.pos_b)):
            if img in checked:
                continue
            checked.add(img)
            cx, cy = centroid(img, *pos)
            assert math.dist((cx, cy), pos) < 1.5, (img.name, pos, (cx, cy))
    assert len(checked) == 7  # every visit of both targets


def test_shift_matches_the_backend_expected_shift():
    for p in game_pairs():
        assert p.shift_px * p.pixel_arcsec == pytest.approx(p.expected_shift_arcsec, abs=0.3), p.pair_id


def test_world_to_png_inverts_png_to_world():
    wcs = {"crval1": 331.09, "crval2": -56.80, "crpix1": 300.5, "crpix2": 300.5,
           "cd1_1": -1 / 7200, "cd1_2": 0.0, "cd2_1": 0.0, "cd2_2": 1 / 7200}
    for px, py in ((0, 0), (299, 299), (123, 456), (599, 10)):
        ra, dec = png_to_world(wcs, 600, px, py)
        x, y = world_to_png(wcs, 600, ra, dec)
        assert (x, y) == pytest.approx((px + 0.5, py + 0.5), abs=1e-6)


def test_distance_to_segment():
    assert distance_to_segment((5, 3), (0, 0), (10, 0)) == pytest.approx(3)
    assert distance_to_segment((-3, 4), (0, 0), (10, 0)) == pytest.approx(5)
    assert distance_to_segment((1, 1), (0, 0), (0, 0)) == pytest.approx(math.sqrt(2))


@pytest.mark.parametrize(
    "d, t, expected",
    [
        (0, 0, 1000),
        (0, 30_000, 800),
        (0, 600_000, 800),
        (HIT_RADIUS_PX, 30_000, 500),
        (10, 15_000, 750),
        (HIT_RADIUS_PX + 0.1, 0, 0),
    ],
)
def test_round_score(d, t, expected):
    assert round_score(d, t) == expected


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #


@pytest.fixture
def game(tmp_path):
    clock = Clock()
    service = GameService(game_pairs(), GameSettings(), CropStore(tmp_path / "game", 300, salt="t",
                                                                   static_root=tmp_path), clock=clock)
    scores = ScoreService(None, InMemoryScoreRepository())
    limiters = {k: SlidingWindowLimiter(1000, 60) for k in ("sessions", "guess", "finish")}
    app.dependency_overrides[get_game_service] = lambda: service
    app.dependency_overrides[get_score_service] = lambda: scores
    app.dependency_overrides[get_game_limiters] = lambda: limiters
    client = TestClient(app)
    client.service, client.clock, client.limiters, client.root = service, clock, limiters, tmp_path
    yield client
    app.dependency_overrides.clear()


def local(client, url: str) -> Path:
    return client.root / url.split("/static/", 1)[1]


def new_session(client, **body):
    res = client.post(f"{BASE}/sessions", json=body or None)
    assert res.status_code == 201, res.text
    return res.json()


def truth(client, session_id, round_id):
    state = client.service._sessions[UUID(session_id)].rounds[round_id]
    return state.pos_b, state.pos_a


def test_session_shape(game):
    s = new_session(game)
    assert len(s["rounds"]) == 5 and "1000" in s["scoring"]
    assert [r["difficulty"] for r in s["rounds"]] == ["easy", "medium", "medium", "medium", "hard"]
    for r in s["rounds"]:
        assert r["target_id"] in {"BARNARDS_STAR", "EPSILON_INDI_B"}
        assert r["image_size"] == {"width": 300, "height": 300}
        assert r["image_a_url"].startswith("http://testserver/static/game/")
        assert (r["hint_image_url"] is not None) == r["weak_signal"]
        for key in ("image_a_url", "image_b_url"):
            with Image.open(local(game, r[key])) as img:
                assert img.size == (300, 300)
        # nothing in the public round gives the answer away
        assert not {"answer", "pos_a", "pos_b", "x", "y"} & set(r)
    assert len({r["round_id"] for r in s["rounds"]}) == 5


@pytest.mark.parametrize("n", [3, 4])
def test_shorter_sessions(game, n):
    rounds = new_session(game, rounds=n)["rounds"]
    assert len(rounds) == n
    assert rounds[0]["difficulty"] == "easy" and rounds[-1]["difficulty"] == "hard"


@pytest.mark.parametrize("n", [2, 6])
def test_session_length_is_bounded(game, n):
    assert game.post(f"{BASE}/sessions", json={"rounds": n}).status_code == 422


def test_crops_move_the_star_away_from_the_centre(game):
    seen = set()
    for _ in range(6):
        new_session(game)
    for state in [st for s in game.service._sessions.values() for st in s.rounds.values()]:
        for x, y in (state.pos_a, state.pos_b):
            assert 30 <= x <= 270 and 30 <= y <= 270  # inside the crop with a margin
        seen.add((round(state.pos_b[0]), round(state.pos_b[1])))
    assert len(seen) > 5  # not always the same place


def test_crops_keep_the_star_where_the_truth_says(game):
    s = new_session(game)
    for r in s["rounds"]:
        pos_b, pos_a = truth(game, s["session_id"], r["round_id"])
        for url, pos in ((r["image_b_url"], pos_b), (r["image_a_url"], pos_a)):
            assert math.dist(centroid(local(game, url), *pos), pos) < 1.5


def guess(client, s, r, x, y, time_ms=5000):
    return client.post(f"{BASE}/guess", json={"session_id": s["session_id"], "round_id": r["round_id"],
                                             "x": x, "y": y, "time_ms": time_ms})


def test_hit_miss_and_one_attempt_per_round(game):
    s = new_session(game)
    r0, r1 = s["rounds"][0], s["rounds"][1]
    pos_b, pos_a = truth(game, s["session_id"], r0["round_id"])
    mid = ((pos_a[0] + pos_b[0]) / 2, (pos_a[1] + pos_b[1]) / 2)

    res = guess(game, s, r0, *mid, time_ms=0)
    assert res.status_code == 200
    body = res.json()
    assert body["hit"] is True and body["distance_px"] == 0 and body["score"] == 1000
    assert body["answer"] == {"x": round(mid[0], 1), "y": round(mid[1], 1)}
    assert len(body["answer_path"]) == 2
    assert "Barnard's Star moved about 10.2″ in 359 days" in body["explanation_es"]
    assert "Gaia DR3" in body["explanation_es"]

    again = guess(game, s, r0, *mid)
    assert again.status_code == 409 and "one attempt" in again.json()["detail"]

    far = (0.5, 0.5) if mid[0] > 100 else (299, 299)
    miss = guess(game, s, r1, *far).json()
    assert miss["hit"] is False and miss["score"] == 0 and miss["distance_px"] > HIT_RADIUS_PX
    assert miss["explanation_es"].startswith("Not this time")


def test_weak_signal_rounds_say_so(game):
    s = new_session(game)
    hard = s["rounds"][-1]
    body = guess(game, s, hard, 150, 150).json()
    assert "less than one SPHEREx pixel (6.15″)" in body["explanation_es"]


@pytest.mark.parametrize("x, y", [(300, 10), (10, 300), (-1, 10), (1e9, 1)])
def test_guess_must_be_inside_the_image(game, x, y):
    s = new_session(game)
    assert guess(game, s, s["rounds"][0], x, y).status_code == 422


def test_unknown_session_and_round(game):
    s = new_session(game)
    fake = {**s, "session_id": "00000000-0000-4000-8000-000000000000"}
    assert guess(game, fake, s["rounds"][0], 1, 1).status_code == 404
    assert guess(game, s, {"round_id": "nope"}, 1, 1).status_code == 404


def test_expired_session(game):
    s = new_session(game)
    game.clock.t += GameSettings().session_ttl_s + 1
    res = guess(game, s, s["rounds"][0], 1, 1)
    assert res.status_code == 410 and res.json()["code"] == "http_410"


def test_guess_extends_the_session(game):
    s = new_session(game)
    ttl = GameSettings().session_ttl_s
    game.clock.t += ttl - 10
    assert guess(game, s, s["rounds"][0], 1, 1).status_code == 200
    game.clock.t += ttl - 10
    assert guess(game, s, s["rounds"][1], 1, 1).status_code == 200


def finish(client, s, nickname="Tombaugh_30", **extra):
    return client.post(f"{BASE}/finish", json={"session_id": s["session_id"], "nickname": nickname, **extra})


def test_score_is_computed_on_the_server(game):
    s = new_session(game)
    total = 0
    for i, r in enumerate(s["rounds"][:3]):
        pos_b, pos_a = truth(game, s["session_id"], r["round_id"])
        total += guess(game, s, r, pos_a[0] + i, pos_a[1], time_ms=4000 * i).json()["score"]
    # the client cannot send a score
    assert finish(game, s, score=999999).status_code == 422
    res = finish(game, s)
    assert res.status_code == 200
    body = res.json()
    assert body["score"] == total and body["hits"] == 3
    assert (body["rounds_played"], body["rounds_total"], body["stored_in"]) == (3, 5, "memory")

    board = game.get(f"{BASE}/leaderboard").json()
    assert board["backend"] == "memory"
    assert board["entries"][0]["nickname"] == "Tombaugh_30" and board["entries"][0]["score"] == total

    assert finish(game, s).status_code == 409  # only once
    assert guess(game, s, s["rounds"][4], 1, 1).status_code == 409  # no play after finishing


def test_finish_needs_at_least_one_round(game):
    assert finish(game, new_session(game)).status_code == 409


@pytest.mark.parametrize("nickname", ["ab", "x" * 17, "no spaces", "<script>", "emoji🚀", "a;drop"])
def test_invalid_nickname(game, nickname):
    s = new_session(game)
    guess(game, s, s["rounds"][0], 1, 1)
    res = finish(game, s, nickname)
    assert res.status_code == 422 and res.json()["code"] == "validation_error"


@pytest.mark.parametrize("nickname", ["Ana", "Ñandú_99", "star-hunter", "  Plutón1930  "])
def test_valid_nickname(game, nickname):
    s = new_session(game)
    guess(game, s, s["rounds"][0], 1, 1)
    assert finish(game, s, nickname).json()["nickname"] == nickname.strip()


def test_leaderboard_is_sorted_and_capped(game):
    for i in range(25):
        s = new_session(game, rounds=3)
        r = s["rounds"][0]
        pos_b, pos_a = truth(game, s["session_id"], r["round_id"])
        guess(game, s, r, pos_a[0] + (i % 19), pos_a[1], time_ms=1000 * i)
        finish(game, s, f"jugador{i:02d}")
    entries = game.get(f"{BASE}/leaderboard").json()["entries"]
    assert len(entries) == 20
    scores = [e["score"] for e in entries]
    assert scores == sorted(scores, reverse=True)


def test_rate_limit_on_sessions(game):
    game.limiters["sessions"] = SlidingWindowLimiter(1, 60)
    assert game.post(f"{BASE}/sessions").status_code == 201
    res = game.post(f"{BASE}/sessions")
    assert res.status_code == 429 and "Retry-After" in res.headers


# --------------------------------------------------------------------------- #
# Leaderboard storage fallback
# --------------------------------------------------------------------------- #


def test_without_supabase_the_leaderboard_is_in_memory():
    assert build_score_service().backend == "memory"


def test_supabase_failure_falls_back_to_memory():
    class Broken:
        def add(self, entry):
            raise ConnectionError("down")

        def top(self, limit):
            raise ConnectionError("down")

    service = ScoreService(Broken(), InMemoryScoreRepository())
    assert service.backend == "supabase"
    entry, backend = service.add("Clyde", 900, 1, 1, 3)
    assert backend == "memory" and service.backend == "memory"
    assert [e.nickname for e in service.top()] == ["Clyde"]


# --------------------------------------------------------------------------- #
# WISE vs SPHEREx round pool (app/data/game_pool/manifest.json)
# --------------------------------------------------------------------------- #


def _write_pool(root: Path, n_easy: int = 3, n_medium: int = 5, n_hard: int = 0) -> Path:
    import json

    static = root / "static" / "game_pool"
    static.mkdir(parents=True)
    rounds = []
    for i, level in enumerate(["easy"] * n_easy + ["medium"] * n_medium + ["hard"] * n_hard):
        names = {}
        for tag in ("a", "b"):
            names[tag] = f"{i:02d}{tag}" + "0" * 20 + ".png"
            Image.new("L", (300, 300)).save(static / names[tag])
        rounds.append({
            "round_key": f"k{i:02d}", "gaia_source_id": str(1000 + i), "name": f"Star  {i}", "difficulty": level,
            "image_a": names["a"], "image_b": names["b"], "telescope_a": "SPHEREx", "telescope_b": "WISE",
            "date_a": "2025-11-01T00:00:00Z", "date_b": "2010-07-02T00:00:00Z",
            "date_a_range": ["2025-07-01T00:00:00Z", "2026-03-01T00:00:00Z"],
            "date_b_range": ["2010-01-07T00:00:00Z", "2011-02-01T00:00:00Z"],
            "delta_t_days": 5600.0, "expected_shift_arcsec": 40.0,
            "pos_a": [150.0, 100.0], "pos_b": [150.0, 166.0], "image_size": 300,
            "pixel_scale_arcsec": 0.6, "spherex_pixel_scale_arcsec": 12.9,
            "region": {"ra_deg": 10.0, "dec_deg": -20.0, "fov_arcmin": 3.0},
        })
    manifest = root / "game_pool.json"
    manifest.write_text(json.dumps({"version": 1, "rounds": rounds}))
    return manifest


@pytest.fixture
def pool_game(tmp_path):
    from app.services.game import load_pool

    manifest = _write_pool(tmp_path)
    pool = load_pool(manifest, tmp_path / "static" / "game_pool")
    service = GameService(game_pairs(), GameSettings(), CropStore(tmp_path / "game", 300, salt="t",
                                                                   static_root=tmp_path), pool=pool)
    app.dependency_overrides[get_game_service] = lambda: service
    app.dependency_overrides[get_game_limiters] = lambda: {k: SlidingWindowLimiter(1000, 60)
                                                           for k in ("sessions", "guess", "finish")}
    client = TestClient(app)
    client.service = service
    yield client
    app.dependency_overrides.clear()


def test_pool_sessions_draw_distinct_pool_rounds(pool_game):
    for _ in range(20):
        s = new_session(pool_game)
        assert s["fallback"] is False
        rounds = s["rounds"]
        assert len({r["target_id"] for r in rounds}) == 5  # no repeats within a session
        assert all(r["source"] == "wise_vs_spherex" and r["image_a_url"].startswith("http://testserver/static/game_pool/")
                   for r in rounds)
        assert rounds[0]["difficulty"] == "easy" and {r["difficulty"] for r in rounds} == {"easy", "medium"}
        r = rounds[0]
        assert r["telescope_b"] == "WISE" and r["spherex_pixel_scale_arcsec"] == 12.9
        assert r["region"] == {"ra_deg": 10.0, "dec_deg": -20.0, "fov_arcmin": 3.0}
        assert len(r["date_a_range"]) == 2 and r["target_name"] == f"Star {r['target_id'][-2:].lstrip('0') or '0'}"
        assert not {"answer", "pos_a", "pos_b", "x", "y"} & set(r)


def test_pool_guess_uses_the_round_hit_radius(pool_game):
    s = new_session(pool_game)
    r = s["rounds"][1]
    assert r["hit_radius_px"] == 25.0  # 15" at 0.6"/px
    res = pool_game.post(f"{BASE}/guess", json={"session_id": s["session_id"], "round_id": r["round_id"],
                                                "x": 150 + 24, "y": 130, "time_ms": 1000}).json()
    assert res["hit"] and res["hit_radius_px"] == 25.0 and "WISE (2010)" in res["explanation_es"]
    assert res["answer_path"] == [{"x": 150.0, "y": 166.0}, {"x": 150.0, "y": 100.0}]
    miss = pool_game.post(f"{BASE}/guess", json={"session_id": s["session_id"], "round_id": s["rounds"][2]["round_id"],
                                                 "x": 150 + 26, "y": 130, "time_ms": 1000}).json()
    assert not miss["hit"] and miss["score"] == 0


@pytest.mark.parametrize("pool", [(), "small"])
def test_missing_or_small_pool_falls_back_to_the_golden_path(tmp_path, pool):
    from app.services.game import load_pool

    if pool == "small":
        pool = load_pool(_write_pool(tmp_path, n_easy=1, n_medium=1), tmp_path / "static" / "game_pool")
    service = GameService(game_pairs(), GameSettings(), CropStore(tmp_path / "game", 300, salt="t",
                                                                   static_root=tmp_path), pool=pool)
    s = service.create(5)
    assert s.fallback is True
    assert {r.target_id for r in s.rounds} <= {"BARNARDS_STAR", "EPSILON_INDI_B"}
    assert {r.source for r in s.rounds} == {"golden_path"}


def test_load_pool_skips_rounds_without_images_and_bad_files(tmp_path):
    from app.services.game import load_pool

    manifest = _write_pool(tmp_path)
    static = tmp_path / "static" / "game_pool"
    next(static.glob("00a*.png")).unlink()
    assert len(load_pool(manifest, static)) == 7
    assert load_pool(tmp_path / "nope.json", static) == ()
    manifest.write_text("{not json")
    assert load_pool(manifest, static) == ()


def test_without_a_pool_fallback_is_null(game):
    assert new_session(game)["fallback"] is None
