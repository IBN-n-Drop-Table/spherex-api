"""Golden Path cache: pre-processed targets served without hitting NASA.

To add one of the other mandatory targets:
  1. Drop its contract JSON into app/data/<key>.json
  2. Add a GoldenPathTarget entry to TARGETS below.
Everything is validated against SkyPatchResponse when the app starts.
"""

import json
from dataclasses import dataclass
from functools import lru_cache

from app.core.config import settings
from app.schemas.sky_patch import SkyPatchResponse


@dataclass(frozen=True)
class GoldenPathTarget:
    key: str  # canonical id, e.g. "WISE_0855"
    data_file: str  # file name inside app/data/
    ra_range: tuple[float, float]  # inclusive (min, max) in degrees
    dec_range: tuple[float, float]  # inclusive (min, max) in degrees

    def contains(self, ra: float, dec: float) -> bool:
        return (
            self.ra_range[0] <= ra <= self.ra_range[1]
            and self.dec_range[0] <= dec <= self.dec_range[1]
        )


TARGETS: tuple[GoldenPathTarget, ...] = (
    GoldenPathTarget(
        key="WISE_0855",
        data_file="wise_0855.json",
        ra_range=(133.7, 133.8),
        dec_range=(-7.3, -7.2),
    ),
)


def normalize_target(name: str) -> str:
    """'wise 0855', 'WISE-0855' and 'WISE_0855' all map to 'WISE_0855'."""
    return "_".join(name.strip().upper().replace("-", " ").split())


@lru_cache
def _load(target: GoldenPathTarget) -> SkyPatchResponse:
    raw = json.loads((settings.data_dir / target.data_file).read_text("utf-8"))
    return SkyPatchResponse.model_validate(raw)


def warm_cache() -> None:
    """Load and validate every cached target. Call once at startup."""
    for target in TARGETS:
        _load(target)


def find_sky_patch(
    target: str | None = None,
    ra: float | None = None,
    dec: float | None = None,
) -> SkyPatchResponse | None:
    """Return the cached patch matching the target name OR the coordinates."""
    wanted = normalize_target(target) if target else None
    for entry in TARGETS:
        if wanted == entry.key:
            return _load(entry)
        if ra is not None and dec is not None and entry.contains(ra, dec):
            return _load(entry)
    return None
