"""Serve-time annotations: science caveats added to responses on the way out.

Applied by app/routers/common.py to every sky patch and spectrum the API
returns (cached, live, fallback and job results). The Golden Path files are
never modified; the inputs are the response itself and the static table in
app/core/science_notes.py.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.core.science_notes import SCIENCE_NOTES, ScienceNote
from app.schemas.sky_patch import LightCurvePoint, SkyPatchResponse
from app.schemas.spectrum import SpectrumResponse
from app.services.golden_path import normalize_target


def note_for(target_name: str | None) -> ScienceNote | None:
    return SCIENCE_NOTES.get(normalize_target(target_name)) if target_name else None


def _with_note(point: LightCurvePoint, extra: str | None) -> LightCurvePoint:
    if not extra or (point.note and extra in point.note):
        return point
    note = f"{point.note} {extra}" if point.note else extra
    return point.model_copy(update={"note": note})


def annotate_patch(patch: SkyPatchResponse) -> SkyPatchResponse:
    note = note_for(patch.target_info.name)
    if note is None:
        return patch
    return patch.model_copy(update={
        "science_note": note.science_note,
        "quality": note.quality,
        "light_curve": [_with_note(p, note.light_curve_note) for p in patch.light_curve],
    })


def annotate_spectrum(spectrum: SpectrumResponse) -> SpectrumResponse:
    note = note_for(spectrum.target_name) if spectrum.source == "target" else None
    if note is None:
        return spectrum
    return spectrum.model_copy(update={"science_note": note.science_note, "quality": note.quality})


def annotate(model: BaseModel) -> BaseModel:
    """Any response model -> the same model with its serve-time annotations."""
    if isinstance(model, SkyPatchResponse):
        return annotate_patch(model)
    if isinstance(model, SpectrumResponse):
        return annotate_spectrum(model)
    return model
