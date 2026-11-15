"""Guarda de idioma: todo texto que la API envía al usuario debe estar en inglés.

Recorre las respuestas reales (los 5 objetos, chat sin proveedores, juego,
errores) y busca caracteres o palabras típicas del español en cualquier valor
de texto. Las claves JSON y los códigos de máquina no se miran.
"""

import os
import re

for _var in ("GROQ_API_KEY", "DEEPSEEK_API_KEY"):
    os.environ.pop(_var, None)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.legacy_text import LEGACY_ES_TO_EN, english  # noqa: E402
from app.core.targets import CATALOG  # noqa: E402
from app.main import app  # noqa: E402
from app.routers.chat import get_chat_limiter  # noqa: E402
from app.services.chat import SUGGESTIONS, ChatService, get_chat_service  # noqa: E402
from app.core.wow_settings import ChatSettings  # noqa: E402
from app.services.rate_limit import SlidingWindowLimiter  # noqa: E402

SPANISH_CHARS = re.compile(r"[áéíóúñÁÉÍÓÚÑ¿¡]")
SPANISH_WORDS = re.compile(
    r"\b(que|los|las|del|para|una|est[aá]|datos|imagen|estrella|sin|cach[eé]|n[uú]cleo|"
    r"vuelve|partida|ronda|intento|puntos|años|resta|píxel(es)?|"
    # Frequent Spanish words with no accent that are not English words: without them a sentence such as
    # "Objetos catalogados en la imagen" slipped through (found by mutating a visible message).
    r"el|la|en|de|con|por|se|son|hay|como|pero|muy|este|objetos?|catalogad[oa]s?|desconocid[oa]s?|"
    r"consultas?|mostrando|resultados?|guardad[oa]s?|precalculad[oa]s?|ahora|todav[ií]a)\b",
    re.IGNORECASE,
)


def texts(obj, path="$"):
    """Every (json-path, string) in a response, URLs and machine codes excluded."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from texts(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from texts(v, f"{path}[{i}]")
    elif isinstance(obj, str) and not obj.startswith(("http://", "https://", "/")):
        yield path, obj


def assert_english(payload, where):
    for path, text in texts(payload):
        assert not SPANISH_CHARS.search(text), (where, path, text)
        assert not SPANISH_WORDS.search(text), (where, path, text)


@pytest.fixture(scope="module")
def client():
    service = ChatService([], ChatSettings(groq_api_key=None, deepseek_api_key=None))
    app.dependency_overrides[get_chat_service] = lambda: service
    app.dependency_overrides[get_chat_limiter] = lambda: SlidingWindowLimiter(10_000, 60)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.mark.parametrize("key", list(CATALOG))
def test_sky_patch_and_spectrum_are_in_english(client, key):
    for route in ("sky-patch", "spectrum"):
        res = client.get(f"/api/v1/{route}", params={"target": key})
        assert res.status_code == 200
        assert_english(res.json(), f"{route} {key}")


def test_targets_epochs_credits_and_phrases(client):
    for url in ("/api/v1/targets", "/api/v1/loading-phrases", "/api/v1/credits"):
        res = client.get(url)
        assert res.status_code == 200, url
        assert_english(res.json(), url)
    t = CATALOG["WISE_0855"]
    assert_english(client.get("/api/v1/epochs", params={"ra": t.ra_deg, "dec": t.dec_deg}).json(), "epochs")


@pytest.mark.parametrize("target", [None, *CATALOG])
def test_chat_suggestions_and_canned_answers(client, target):
    res = client.get("/api/v1/chat/suggestions", params={"target": target} if target else {})
    assert_english(res.json(), f"suggestions {target}")
    for question in res.json()["suggestions"]:
        body = client.post("/api/v1/chat", json={
            "message": question,
            "context": {"target": target or "", "saturated": True, "weak_signal": True,
                        "quality": "low", "fallback_reason": "irsa_unavailable"},
        }).json()
        assert body["fallback"] is True
        assert_english(body, f"chat {question}")


def test_spanish_questions_get_english_answers(client):
    for question in ("¿Qué es SPHEREx?", "¿Qué es una enana marrón?", "¿Por qué se mueve?",
                     "¿Qué es la resta de imágenes?", "¿A qué distancia está?", "cuéntame algo"):
        body = client.post("/api/v1/chat", json={"message": question,
                                                 "context": {"target": "BARNARDS_STAR"}}).json()
        assert_english({k: v for k, v in body.items() if k != "answer"} | {"answer": body["answer"]},
                       question)


def test_game_texts_and_errors(client):
    s = client.post("/api/v1/game/sessions", json={"rounds": 3}).json()
    assert_english(s, "session")
    r = s["rounds"][-1]
    guess = {"session_id": s["session_id"], "round_id": r["round_id"], "x": 1, "y": 1, "time_ms": 1}
    assert_english(client.post("/api/v1/game/guess", json=guess).json(), "guess")
    assert_english(client.post("/api/v1/game/guess", json=guess).json(), "double guess (409)")
    assert_english(client.post("/api/v1/game/guess", json={**guess, "round_id": "x"}).json(), "404")
    fresh = client.post("/api/v1/game/sessions").json()
    assert_english(client.post("/api/v1/game/finish", json={"session_id": fresh["session_id"],
                                                           "nickname": "Ana"}).json(), "finish 409")


def test_rate_limit_message(client):
    app.dependency_overrides[get_chat_limiter] = lambda: SlidingWindowLimiter(0, 60)
    try:
        res = client.post("/api/v1/chat", json={"message": "hi"})
    finally:
        app.dependency_overrides[get_chat_limiter] = lambda: SlidingWindowLimiter(10_000, 60)
    assert res.status_code == 429
    assert_english(res.json(), "429")


def test_lost_job_message(client):
    res = client.get("/api/v1/jobs/00000000-0000-4000-8000-000000000000")
    assert res.status_code == 404
    assert_english(res.json(), "job 404")


def test_legacy_table_is_complete_and_english():
    for es, en in LEGACY_ES_TO_EN.items():
        assert english(es) == en
        assert not SPANISH_CHARS.search(en) and not SPANISH_WORDS.search(en), en
    assert english("Already English.") == "Already English."
    assert english(None) is None


def test_catalog_objects_texts_and_errors_are_in_english():
    from app.core.simbad_settings import SimbadSettings
    from app.routers.catalog_objects import get_simbad_limiter
    from app.services.catalog_objects import FALLBACK_TEXT
    from app.services.simbad import SimbadRow, SimbadService, SimbadUnavailable, get_simbad_service
    from tests.test_simbad_service import FakeTransport, tap

    url = "/api/v1/catalog-objects"
    ok = FakeTransport(tap(["* 699", "Flare*", "Flare Star", 269.446, 4.767, None, None, 1500, 9.5, None, None,
                            None, None, None]))
    pre = SimbadRow.from_dict({"main_id": "Pre", "otype": "G", "otype_label": "Galaxy", "ra": 269.446,
                               "dec": 4.767, "nbref": 1})

    def serve(transport, gate_limit=1000, precomputed=None):
        svc = SimbadService(SimbadSettings(tap_url="u"), transport=transport,
                            gate=SlidingWindowLimiter(gate_limit, 1.0), precomputed=precomputed or (lambda k: None))
        app.dependency_overrides[get_simbad_service] = lambda: svc
        app.dependency_overrides[get_simbad_limiter] = lambda: SlidingWindowLimiter(10_000, 60)

    with TestClient(app) as c:
        try:
            serve(ok)
            body = c.get(url, params={"target": "BARNARDS_STAR"}).json()
            assert body["objects"]
            assert_english(body, "catalog-objects ok")
            assert "SPHEREx detections" in body["notice"] and "SIMBAD" in body["data_credits"][0]["source"]
            # every fallback wording, through the real endpoint
            serve(FakeTransport(error=SimbadUnavailable("x")), precomputed=lambda k: [pre])
            for params in ({"target": "BARNARDS_STAR"}, {"target": "BARNARDS_STAR", "type_filter": "no_common_stars"},
                           {"target": "WISE_0855", "type_filter": "all"}):
                body = c.get(url, params=params).json()
                assert body["fallback_reason"] == "simbad_unavailable"
                assert_english(body, f"catalog-objects fallback {params}")
            serve(ok, gate_limit=0)
            body = c.get(url, params={"target": "R_DRACONIS"}).json()
            assert body["fallback_reason"] == "simbad_rate_limited"
            assert_english(body, "catalog-objects rate cap")
            for text in FALLBACK_TEXT.values():
                assert not SPANISH_CHARS.search(text) and not SPANISH_WORDS.search(text), text
            # errors: 404, 422 (several), 429
            for params, status in (({"target": "NOPE"}, 404), ({}, 422), ({"target": "BARNARDS_STAR", "max_n": 99}, 422),
                                   ({"target": "BARNARDS_STAR", "type_filter": "x"}, 422),
                                   ({"target": "BARNARDS_STAR", "epoch": "nope"}, 422),
                                   ({"ra": 1, "dec": 1, "fov": 25}, 422)):
                res = c.get(url, params=params)
                assert res.status_code == status, params
                assert_english(res.json(), f"catalog-objects error {params}")
            app.dependency_overrides[get_simbad_limiter] = lambda: SlidingWindowLimiter(0, 60)
            res = c.get(url, params={"target": "BARNARDS_STAR"})
            assert res.status_code == 429
            assert_english(res.json(), "catalog-objects 429")
        finally:
            app.dependency_overrides.pop(get_simbad_service, None)
            app.dependency_overrides.pop(get_simbad_limiter, None)
