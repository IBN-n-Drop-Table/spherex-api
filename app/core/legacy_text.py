"""Traducción al servir de los textos en español guardados en los datos preprocesados.

Los JSON del Golden Path (app/data/*.json) se generaron cuando la interfaz
estaba en español y no se regeneran: `detection_label`, `detection_notice`,
`spectrum_message` y las `note` de la curva de luz llevan esas frases. El
usuario final debe verlas en inglés, así que app/services/annotations.py las
cambia aquí, por coincidencia EXACTA, al servir la respuesta.

Un texto que no esté en la tabla se deja tal cual. tests/test_english_ui.py
comprueba que ninguna respuesta de los 5 objetos lleva ya texto en español.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.schemas.sky_patch import SkyPatchResponse
from app.schemas.spectrum import SpectrumResponse

LEGACY_ES_TO_EN: dict[str, str] = {
    "Detección Asistida por Software (BETA)": "Software-Assisted Detection (BETA)",
    "Anomalía detectada por software: requiere verificación humana":
        "Anomaly detected by software: requires human verification",
    "El núcleo de la fuente satura SPHEREx (flags OVERFLOW/BLOOM): esos píxeles se muestran en las "
    "imágenes pero se excluyen del espectro y de la curva de luz.":
        "The source core saturates SPHEREx (OVERFLOW/BLOOM flags): those pixels are shown in the "
        "images but excluded from the spectrum and the light curve.",
    "Sin fotometría válida: el núcleo satura SPHEREx (OVERFLOW/BLOOM)":
        "No valid photometry: the core saturates SPHEREx (OVERFLOW/BLOOM)",
    "Sin fotometría válida en el centro en esta visita": "No valid photometry at the center in this visit",
    "Sin canales comunes con las otras visitas: media de sus propios canales (aproximada)":
        "No channels in common with the other visits: mean of its own channels (approximate)",
    "Sin fotometría válida en el centro del parche.": "No valid photometry at the center of the patch.",
}


def english(text: str | None) -> str | None:
    return LEGACY_ES_TO_EN.get(text, text) if text else text


def to_english(model: BaseModel) -> BaseModel:
    """Sky patch or spectrum with its stored Spanish texts replaced (other models: unchanged)."""
    if isinstance(model, SpectrumResponse):
        return model.model_copy(update={"spectrum_message": english(model.spectrum_message)})
    if not isinstance(model, SkyPatchResponse):
        return model
    diff = model.difference_imaging
    diff = diff.model_copy(update={"detection_label": english(diff.detection_label),
                                   "detection_notice": english(diff.detection_notice)})
    curve = [p.model_copy(update={"note": english(p.note)}) if p.note else p for p in model.light_curve]
    return model.model_copy(update={"difference_imaging": diff, "light_curve": curve,
                                    "spectrum_message": english(model.spectrum_message)})
