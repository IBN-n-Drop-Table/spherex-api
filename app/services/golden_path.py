"""Golden Path cache: pre-processed targets served without hitting NASA.

Targets live in app/core/targets.py. A target is served once its JSON exists
in app/data/ (written by scripts/generate_golden_path.py). Every JSON is
validated against SkyPatchResponse when the app starts.
"""

import json
import logging
from functools import lru_cache

from app.core.config import settings
from app.core.patch_geometry import angular_separation_arcsec
from app.core.targets import CATALOG, Target
from app.schemas.sky_patch import DetectedAnomaly, SkyPatchResponse
from app.schemas.spectrum import SpectrumResponse

logger = logging.getLogger(__name__)


def normalize_target(name: str) -> str:
    """'wise 0855', 'WISE-0855' and 'WISE_0855' all map to 'WISE_0855'."""
    return "_".join(name.strip().upper().replace("-", " ").replace("'", "").split())


def _available() -> list[Target]:
    return [t for t in CATALOG.values() if (settings.data_dir / t.data_file).is_file()]


@lru_cache
def _load(key: str) -> SkyPatchResponse:
    target = CATALOG[key]
    path = settings.data_dir / target.data_file
    try:
        return SkyPatchResponse.model_validate(json.loads(path.read_text("utf-8")))
    except ValueError as exc:  # pydantic.ValidationError is a ValueError
        raise RuntimeError(
            f"{path.name} does not match the current SkyPatchResponse contract. Regenerate it: "
            f"python scripts/generate_golden_path.py --target {key}"
        ) from exc


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


def catalog_target(name: str) -> Target | None:
    """Catalogue entry for a name, cached or not."""
    return CATALOG.get(normalize_target(name))


class AnomalyNotFound(LookupError):
    pass


def _target_spectrum(patch: SkyPatchResponse) -> SpectrumResponse:
    return SpectrumResponse(
        source="target",
        target_name=patch.target_info.name,
        ra_deg=patch.patch.wcs.crval1,  # photometry follows the star, centred here at epoch 1
        dec_deg=patch.patch.wcs.crval2,
        epoch_id=patch.timelapse_epochs[0].epoch_id,
        is_cached_golden_path=True,
        spectrum_102_bands=patch.spectrum_102_bands,
    )


def _anomaly_spectrum(patch: SkyPatchResponse, anomaly: DetectedAnomaly) -> SpectrumResponse:
    return SpectrumResponse(
        source="anomaly",
        target_name=patch.target_info.name,
        anomaly_id=anomaly.anomaly_id,
        ra_deg=anomaly.ra_deg,
        dec_deg=anomaly.dec_deg,
        epoch_id=anomaly.spectrum_epoch_id,
        is_cached_golden_path=True,
        spectrum_102_bands=anomaly.spectrum_102_bands,
    )


def find_cached_spectrum(
    target: str | None = None,
    anomaly_id: str | None = None,
    ra: float | None = None,
    dec: float | None = None,
    radius_arcsec: float = 6.0,
) -> SpectrumResponse | None:
    """Precomputed spectrum, by name (+ anomaly_id) or by position.

    By position, the closest of every cached patch centre and anomaly within
    `radius_arcsec` wins. Raises AnomalyNotFound for an unknown anomaly_id
    on a cached target.
    """
    wanted = normalize_target(target) if target else None
    patches = [(t, _load(t.key)) for t in _available()]

    if wanted is not None:
        for entry, patch in patches:
            if entry.key != wanted:
                continue
            if anomaly_id is None:
                return _target_spectrum(patch)
            for anomaly in patch.difference_imaging.detected_anomalies:
                if anomaly.anomaly_id == anomaly_id:
                    return _anomaly_spectrum(patch, anomaly)
            raise AnomalyNotFound(f"{anomaly_id} is not an anomaly of {entry.name}")

    if ra is None or dec is None:
        return None
    best: tuple[float, SpectrumResponse] | None = None
    for _, patch in patches:
        wcs = patch.patch.wcs
        options = [(angular_separation_arcsec(ra, dec, wcs.crval1, wcs.crval2), patch, None)]
        options += [(angular_separation_arcsec(ra, dec, a.ra_deg, a.dec_deg), patch, a)
                    for a in patch.difference_imaging.detected_anomalies]
        for distance, p, anomaly in options:
            if distance <= radius_arcsec and (best is None or distance < best[0]):
                spectrum = _anomaly_spectrum(p, anomaly) if anomaly else _target_spectrum(p)
                best = (distance, spectrum)
    return best[1] if best else None
