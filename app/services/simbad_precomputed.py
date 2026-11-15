"""Precomputed SIMBAD rows for the 5 Golden Path targets (app/data/simbad/<target>.json).

A subdirectory on purpose: other code and tests glob app/data/*.json for the sky-patch files.

Written by scripts/generate_simbad_golden.py. The files hold the unfiltered, J2000 rows of
the cone search around each patch, so every type_filter, max_n and epoch is served from them.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.core.config import settings
from app.services.simbad import SimbadRow

logger = logging.getLogger(__name__)


def precomputed_path(key: str, data_dir: Path | None = None) -> Path:
    return (data_dir or settings.data_dir) / "simbad" / f"{key.lower()}.json"


def load_precomputed(key: str, data_dir: Path | None = None) -> list[SimbadRow] | None:
    """The rows saved for a Golden Path target, or None when there is no (valid) file."""
    path = precomputed_path(key, data_dir)
    if not path.is_file():
        return None
    try:
        return [SimbadRow.from_dict(r) for r in json.loads(path.read_text("utf-8"))["objects"]]
    except (ValueError, KeyError, TypeError):
        logger.warning("%s is not a valid precomputed SIMBAD file; ignored", path.name)
        return None
