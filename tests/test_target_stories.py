"""GET /targets: distances and plain-language texts (app/core/target_stories.py)."""

import pytest
from fastapi.testclient import TestClient

from app.core.target_stories import LY_PER_PC, STORIES, story_fields
from app.core.targets import CATALOG
from app.main import app


@pytest.fixture(scope="module")
def targets():
    with TestClient(app) as client:
        res = client.get("/api/v1/targets")
    assert res.status_code == 200
    return {t["key"]: t for t in res.json()}


def test_every_challenge_target_has_a_story():
    assert set(STORIES) == set(CATALOG)


def test_old_fields_unchanged_and_new_fields_present(targets):
    assert set(targets) == set(CATALOG)
    for t in targets.values():
        for old in ("key", "name", "ra_deg", "dec_deg", "fov_arcmin", "is_cached_golden_path",
                    "n_epochs", "sky_patch_query"):
            assert old in t
        for new in ("distance_ly", "distance_source", "light_left_years_ago", "distance_uncertain",
                    "description_es", "story_es"):
            assert t[new] is not None, (t["key"], new)


@pytest.mark.parametrize(
    "key, ly, years",
    [
        # Derived from the cited parallaxes: d[ly] = 1000 / plx[mas] * 3.26156
        ("BARNARDS_STAR", 5.96, 6),
        ("EPSILON_INDI_B", 12.05, 12),
        ("WISE_0855", 7.43, 7),
        ("RW_AURIGAE", 509.13, 510),   # uncertain: 2 significant figures
        ("R_DRACONIS", 2607.34, 2600),  # uncertain: 2 significant figures
    ],
)
def test_distances_follow_from_the_parallax(targets, key, ly, years):
    t = targets[key]
    assert t["distance_ly"] == pytest.approx(ly, abs=0.01)
    assert t["distance_ly"] == pytest.approx(1000 / STORIES[key].parallax_mas * LY_PER_PC, abs=0.01)
    assert t["light_left_years_ago"] == years
    assert f" {years} años" in t["story_es"]


def test_unreliable_parallaxes_are_flagged_and_worded_as_approximate(targets):
    for key in ("R_DRACONIS", "RW_AURIGAE"):
        assert targets[key]["distance_uncertain"] is True
        assert "aproximadamente" in targets[key]["story_es"]
    for key in ("BARNARDS_STAR", "EPSILON_INDI_B", "WISE_0855"):
        assert targets[key]["distance_uncertain"] is False


def test_sources_are_cited(targets):
    for key, t in targets.items():
        src = t["distance_source"]
        assert "mas" in src
        assert ("Gaia DR3" in src and "source_id" in src) or "Kirkpatrick et al. 2021" in src, key


def test_unknown_target_has_no_story():
    assert story_fields("NOT_A_TARGET") == {}
