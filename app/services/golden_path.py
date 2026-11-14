"""Golden Path cache: pre-processed targets served without hitting NASA.

Targets live in app/core/targets.py. A target is served once its JSON exists
in app/data/ (written by scripts/generate_golden_path.py). Every JSON is
validated against SkyPatchResponse when the app starts.
"""

import json
import logging
from functools import lru_cache

from app.core.config import settings
from app.core.targets import CATALOG, Target
from app.schemas.sky_patch import SkyPatchResponse

logger = logging.getLogger(__name__)


def normalize_target(name: str) -> str:
    """'wise 0855', 'WISE-0855' and 'WISE_0855' all map to 'WISE_0855'."""
    return "_".join(name.strip().upper().replace("-", " ").replace("'", "").split())


def _available() -> list[Target]:
    return [t for t in CATALOG.values() if (settings.data_dir / t.data_file).is_file()]


@lru_cache
def _load(key: str) -> SkyPatchResponse:
    target = CATALOG[key]
    raw = json.loads((settings.data_dir / target.data_file).read_text("utf-8"))
    return SkyPatchResponse.model_validate(raw)


def warm_cache() -> None:
    """Load and validate every cached target. Call once at startup."""
    available = _available()
    for target in available:
        _load(target.key)
    missing = sorted(set(CATALOG) - {t.key for t in available})
    if missing:
        logger.warning("Golden Path targets without cached data: %s", ", ".join(missing))


def find_sky_patch(
    target: str | None = None,
    ra: float | None = None,
    dec: float | None = None,
) -> SkyPatchResponse | None:
    """Return the cached patch matching the target name OR the coordinates."""
    wanted = normalize_target(target) if target else None
    for entry in _available():
        if wanted == entry.key:
            return _load(entry.key)
        if ra is not None and dec is not None and entry.contains(ra, dec):
            return _load(entry.key)
    return None
