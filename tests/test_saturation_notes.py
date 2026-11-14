"""Saturated targets: quality "low", lower-limit notes, never a variability claim."""

import pytest
from fastapi.testclient import TestClient

from app.core.science_notes import SATURATED_LIGHT_CURVE_NOTE
from app.core.urls import to_internal
from app.main import app
from app.schemas.sky_patch import SkyPatchResponse
from app.services.annotations import annotate
from tests.conftest import load_raw


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def get(client, path, key):
    return client.get(f"/api/v1/{path}", params={"target": key}).json()


def test_barnards_star_keeps_its_empty_spectrum_and_explains_it(client):
    body = get(client, "sky-patch", "BARNARDS_STAR")
    assert body["saturated"] is True and body["quality"] == "low"
    assert body["spectrum_102_bands"] == [] and "satura" in body["spectrum_message"]
    assert all(p["flux_mjy_sr"] is None and "satura" in p["note"] for p in body["light_curve"])
    assert "10.3″" in body["science_note"] and "10.2″" in body["science_note"]
    spec = get(client, "spectrum", "BARNARDS_STAR")
    assert spec["spectrum_102_bands"] == [] and spec["quality"] == "low"


def test_rw_aurigae_light_curve_is_a_biased_lower_limit(client):
    body = get(client, "sky-patch", "RW_AURIGAE")
    assert body["saturated"] is True and body["quality"] == "low"
    points = [p for p in body["light_curve"] if p["flux_mjy_sr"] is not None]
    assert points and all(SATURATED_LIGHT_CURVE_NOTE in p["note"] for p in points)
    assert body["spectrum_102_bands"] and all(b["quality"] == "low" for b in body["spectrum_102_bands"])
    spec = get(client, "spectrum", "RW_AURIGAE")
    assert spec["quality"] == "low" and all(b["quality"] == "low" for b in spec["spectrum_102_bands"])
    assert "lower limits" in spec["science_note"]


def test_r_draconis_says_the_halo_dims_but_the_core_saturates(client):
    body = get(client, "sky-patch", "R_DRACONIS")
    assert body["saturated"] is True and body["quality"] == "low"
    assert body["spectrum_102_bands"] == [] and body["spectrum_message"]
    for p in body["light_curve"]:
        assert p["flux_mjy_sr"] is None
        assert "No valid photometry" in p["note"]  # the pipeline's own note is kept
        assert "~0.56" in p["note"] and "Mira" in p["note"] and "saturated" in p["note"]
    assert "~0.56" in body["science_note"]


def test_a_clean_object_gets_no_note(client):
    body = get(client, "sky-patch", "EPSILON_INDI_B")
    assert body["saturated"] is False
    assert body["science_note"] is None and body["quality"] is None
    assert all(p["note"] is None for p in body["light_curve"])
    assert all(b["quality"] is None for b in body["spectrum_102_bands"])
    spec = get(client, "spectrum", "EPSILON_INDI_B")
    assert spec["science_note"] is None and spec["quality"] is None


def test_the_saturation_rule_applies_to_any_patch_live_ones_too():
    raw = to_internal(load_raw("rw_aurigae.json"))
    raw["target_info"].update(name="RA 77.0000 Dec +30.4000", is_cached_golden_path=False)  # a live job
    out = annotate(SkyPatchResponse.model_validate(raw))
    assert out.science_note is None  # no per-object note for an arbitrary position...
    assert out.quality == "low"  # ...but the rule still holds
    assert all(b.quality == "low" for b in out.spectrum_102_bands)
    assert all(SATURATED_LIGHT_CURVE_NOTE in p.note for p in out.light_curve if p.flux_mjy_sr is not None)


def test_annotating_twice_does_not_repeat_notes():
    patch = SkyPatchResponse.model_validate(to_internal(load_raw("r_draconis.json")))
    once = annotate(patch)
    assert annotate(once).light_curve == once.light_curve
