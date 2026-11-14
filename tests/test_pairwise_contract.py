"""Consecutive-visit differences in the cached Golden Path (no science stack needed)."""

import hashlib
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.targets import CATALOG
from app.main import app
from app.schemas.sky_patch import SkyPatchResponse
from tests.conftest import load_raw

# The first-vs-last difference images must stay byte-identical when the
# Golden Path is regenerated (pairwise differences are additive). These are
# the PNGs committed before pairwise differences existed.
FIRST_VS_LAST_SHA256 = {
    "barnardsstar_diff.png": "c22151973415c042e65f57a3b908e3a289015a1e5f3c944d7fc525870e49e776",
    "epsilonindib_diff.png": "a1b0488383e2ec6c2d4df4afbee925e921bbf82d83a36a6b285225956f7645b6",
    "rdraconis_diff.png": "2b30c4d474dd98aa21059a956424ac7496eb969ef85156861ba21c2b0f546433",
    "rwaurigae_diff.png": "2ce3881e4c00f81978fc5a215f356ad63707648f9f6987cc984ffbb637d5f2e1",
    "wise0855_diff.png": "ddfde1e872198d546b44196c0d2ddd88f7ea3388e7cea2d8da0f9c897643fba8",
}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize("name", sorted(FIRST_VS_LAST_SHA256))
def test_first_vs_last_images_are_unchanged(name):
    data = (settings.static_dir / "golden_path" / name).read_bytes()
    assert hashlib.sha256(data).hexdigest() == FIRST_VS_LAST_SHA256[name]


@pytest.mark.parametrize("key", sorted(CATALOG))
def test_cached_targets_have_consecutive_pairs(client, key):
    body = client.get("/api/v1/sky-patch", params={"target": key}).json()
    diff = body["difference_imaging"]
    ids = [e["epoch_id"] for e in body["timelapse_epochs"]]
    assert diff["kind"] == "first_vs_last"
    assert (diff["epoch_b"], diff["epoch_a"]) == (ids[0], ids[-1])
    assert diff["subtracted_image_url"].endswith(f"{CATALOG[key].slug}_diff.png")
    pairs = diff["pairwise"]
    assert [(p["epoch_b"], p["epoch_a"]) for p in pairs] == (list(zip(ids, ids[1:])) if len(ids) >= 3 else [])
    for p in pairs:
        assert p["kind"] == "consecutive"
        assert p["image_format"] == "png"
        assert p["delta_t_days"] > 0 and p["detectors_used"] and isinstance(p["weak_signal"], bool)
        assert p["subtracted_image_url"].startswith("http://testserver/static/golden_path/")
        assert not p["subtracted_image_url"].endswith("_diff.png")  # never the first-vs-last image


@pytest.mark.parametrize("key", sorted(CATALOG))
def test_pair_images_are_served_with_cors(client, key):
    body = client.get("/api/v1/sky-patch", params={"target": key}).json()
    for p in body["difference_imaging"]["pairwise"]:
        res = client.get(urlsplit(p["subtracted_image_url"]).path, headers={"Origin": "https://x.vercel.app"})
        assert res.status_code == 200
        assert res.headers["content-type"] == "image/png"
        assert res.headers["access-control-allow-origin"] == "*"
        assert res.content[16:24] == (600).to_bytes(4, "big") * 2  # PNG IHDR: 600 x 600


def test_files_without_the_new_fields_still_validate():
    raw = load_raw("wise_0855.json")
    for k in ("kind", "delta_t_days", "detectors_used", "expected_shift_arcsec", "weak_signal", "pairwise"):
        raw["difference_imaging"].pop(k, None)
    from app.core.urls import to_internal

    model = SkyPatchResponse.model_validate(to_internal(raw))
    assert model.difference_imaging.kind == "first_vs_last"
    assert model.difference_imaging.pairwise == []


def test_pairs_must_name_two_different_timelapse_epochs():
    from app.core.urls import to_internal

    raw = load_raw("epsilon_indi_b.json")
    raw["difference_imaging"]["pairwise"][0]["epoch_a"] = "2099_M0"
    with pytest.raises(ValueError, match="pairwise epoch_a and epoch_b"):
        SkyPatchResponse.model_validate(to_internal(raw))

