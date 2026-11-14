"""Image URLs: relative on disk, absolute on the wire.

Cached JSON files (app/data/*.json) and live-job outputs store image paths
relative to the API, e.g. ``/static/golden_path/wise0855_diff.png``, so the
same files work on localhost, on Render and behind any proxy.

Inside the process, paths are prefixed with INTERNAL_BASE so the Pydantic
models (which validate image URLs as ``HttpUrl``) accept them. Just before
a response leaves, INTERNAL_BASE is swapped for the public base: the
``PUBLIC_BASE`` env var if set, otherwise the request's own base URL. Clients
therefore keep receiving absolute URLs, as before.

URLs that point elsewhere (e.g. an optional Supabase mirror) are left alone.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

STATIC_PREFIX = "/static/"
INTERNAL_BASE = "http://spherex.internal"


def _walk(obj: Any, fn) -> Any:
    if isinstance(obj, dict):
        return {k: _walk(v, fn) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_walk(v, fn) for v in obj]
    if isinstance(obj, str):
        return fn(obj)
    return obj


def to_internal(payload: Any) -> Any:
    """Relative '/static/...' paths -> INTERNAL_BASE URLs (for model validation)."""
    return _walk(payload, lambda s: INTERNAL_BASE + s if s.startswith(STATIC_PREFIX) else s)


def to_relative(payload: Any) -> Any:
    """INTERNAL_BASE URLs -> relative '/static/...' paths (for files on disk)."""
    n = len(INTERNAL_BASE)
    return _walk(payload, lambda s: s[n:] if s.startswith(INTERNAL_BASE + STATIC_PREFIX) else s)


def normalize_base(base: str) -> str:
    """'https://x.onrender.com/', '.../static' and '.../static/' all mean the server origin."""
    base = base.strip().rstrip("/")
    if base.endswith("/static"):
        base = base[: -len("/static")]
    return base


def to_public(payload: BaseModel | Any, base: str) -> Any:
    """Model or dict -> JSON-ready dict with absolute image URLs under `base`."""
    if isinstance(payload, BaseModel):
        payload = payload.model_dump(mode="json")
    root = normalize_base(base)
    n = len(INTERNAL_BASE)
    return _walk(payload, lambda s: root + s[n:] if s.startswith(INTERNAL_BASE + STATIC_PREFIX) else s)
