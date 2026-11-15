"""GET /api/v1/loading-phrases: frases de carga en inglés, sin dependencias."""

import ast
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.loading_phrases import LOADING_PHRASES
from app.main import app

CATEGORIES = {"generic", "cold_start", "subtraction", "game", "chat"}
FORBIDDEN = set("¿¡ñÑáéíóúÁÉÍÓÚüÜ‘’“”…")


@pytest.fixture(scope="module")
def response():
    with TestClient(app) as client:
        return client.get("/api/v1/loading-phrases")


def test_ok_and_cacheable(response):
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=3600"


def test_five_non_empty_categories(response):
    phrases = response.json()["phrases"]
    assert set(phrases) == CATEGORIES
    assert all(len(v) > 0 for v in phrases.values())
    assert phrases == {k: list(v) for k, v in LOADING_PHRASES.items()}


def test_no_duplicates_and_no_empty_phrases(response):
    every = [p for v in response.json()["phrases"].values() for p in v]
    assert len(every) == len(set(every))
    assert all(p.strip() and p == p.strip() for p in every)


def test_plain_english_ascii(response):
    for phrase in (p for v in response.json()["phrases"].values() for p in v):
        assert phrase.isascii(), phrase
        assert not FORBIDDEN & set(phrase), phrase


def test_module_has_no_app_dependencies():
    """It must answer while everything else (DB, AI providers, cached data) is down."""
    for path in ("app/core/loading_phrases.py", "app/routers/loading_phrases.py"):
        tree = ast.parse(Path(path).read_text("utf-8"))
        imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        app_modules = {m for m in imported if m and m.startswith("app")}
        assert app_modules <= {"app.core.loading_phrases"}, (path, app_modules)
