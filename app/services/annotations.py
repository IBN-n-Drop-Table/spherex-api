"""Serve-time annotations: science caveats added to responses on the way out.

Applied by app/routers/common.py to every sky patch and spectrum the API
returns (cached, live, fallback and job results). The Golden Path files are
never modified; the inputs are the response itself and the static table in
app/core/science_notes.py.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.core.credits import DATA_CREDITS
from app.schemas.credits import DataCredit
from app.core.science_notes import SATURATED_LIGHT_CURVE_NOTE, SCIENCE_NOTES, ScienceNote
from app.schemas.sky_patch import LightCurvePoint, SkyPatchResponse, SpectralBand
from app.schemas.spectrum import SpectrumResponse
from app.services.golden_path import normalize_target

# Validated once: model_copy(update=...) does not validate, so pass models, not dicts.
CREDITS = [DataCredit.model_validate(c) for c in DATA_CREDITS]


def note_for(target_name: str | None) -> ScienceNote | None:
    return SCIENCE_NOTES.get(normalize_target(target_name)) if target_name else None


def _with_note(point: LightCurvePoint, extra: str | None) -> LightCurvePoint:
    if not extra or (point.note and extra in point.note):
        return point
    note = f"{point.note} {extra}" if point.note else extra
    return point.model_copy(update={"note": note})


def _low(bands: list[SpectralBand]) -> list[SpectralBand]:
    return [b.model_copy(update={"quality": "low"}) for b in bands]


def _quality(note: ScienceNote | None, saturated: bool | None) -> str | None:
    if note is not None and note.quality is not None:
        return note.quality
    return "low" if saturated else None


def _light_curve_point(point: LightCurvePoint, saturated: bool | None, note: ScienceNote | None) -> LightCurvePoint:
    if saturated and point.flux_mjy_sr is not None:  # null points already explain themselves
        point = _with_note(point, SATURATED_LIGHT_CURVE_NOTE)
    return _with_note(point, note.light_curve_note if note else None)


def annotate_patch(patch: SkyPatchResponse) -> SkyPatchResponse:
    """Per-object note (science_notes.py) + the saturation rule, for any patch:
    `saturated: true` -> quality "low" on the response and on every spectral
    band, and a lower-limit note on light-curve points that have a flux."""
    note = note_for(patch.target_info.name)
    if note is None and not patch.saturated:
        return patch
    update = {
        "science_note": note.science_note if note else None,
        "quality": _quality(note, patch.saturated),
        "light_curve": [_light_curve_point(p, patch.saturated, note) for p in patch.light_curve],
    }
    if patch.saturated:
        update["spectrum_102_bands"] = _low(patch.spectrum_102_bands)
    return patch.model_copy(update=update)


def annotate_spectrum(spectrum: SpectrumResponse) -> SpectrumResponse:
    if spectrum.source != "target":  # anomaly spectra are about other sources
        return spectrum
    note = note_for(spectrum.target_name)
    if note is None and not spectrum.saturated:
        return spectrum
    update = {"science_note": note.science_note if note else None,
              "quality": _quality(note, spectrum.saturated)}
    if spectrum.saturated:
        update["spectrum_102_bands"] = _low(spectrum.spectrum_102_bands)
    return spectrum.model_copy(update=update)


def annotate(model: BaseModel) -> BaseModel:
    """Any response model -> the same model with its serve-time annotations
    (science notes, quality, and the data credits every patch/spectrum carries)."""
    if isinstance(model, SkyPatchResponse):
        model = annotate_patch(model)
    elif isinstance(model, SpectrumResponse):
        model = annotate_spectrum(model)
    else:
        return model
    return model.model_copy(update={"data_credits": CREDITS})
