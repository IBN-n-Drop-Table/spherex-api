"""Static, per-object science caveats, added to responses when they are SERVED.

The pre-processed Golden Path files (app/data/*.json) and PNGs are never
edited for this: app/services/annotations.py reads this table and fills
`science_note`, `quality` and light-curve `note`s on the way out.

Every statement here comes from measurements made on 2026-11-14 on the real
SPHEREx data (60 exposures per visit, same pipeline). Do not add claims that
were not measured.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Quality = Literal["unvalidated", "low"]


@dataclass(frozen=True)
class ScienceNote:
    science_note: str  # shown with the sky patch and the target spectrum (Spanish)
    quality: Quality | None = None  # response-level quality; None = not flagged here
    light_curve_note: str | None = None  # added to every light-curve point


WISE_0855_NOTE = (
    "Datos sin validar. En la banda ancha, WISE 0855 está mezclado con una estrella fija vecina "
    "(a ~11″), que domina en 1 µm: el espectro y la curva de luz de este parche no deben "
    "interpretarse como una detección ni como el espectro de una enana Y. Su movimiento solo está "
    "confirmado en el detector 6 (4,4–5 µm): el centroide se desplaza 7,9″ al oeste entre la primera "
    "y la última visita, frente a 8,05″ esperados. Sí se puede afirmar que el parche sigue su "
    "movimiento propio: está centrado en su posición de la primera visita, ~2′ al oeste de la "
    "posición de catálogo de 2010."
)

# Any response with `saturated: true` (cached or live) gets this on every light-curve
# point that has a flux: photometry used only the unsaturated pixels of the aperture.
SATURATED_LIGHT_CURVE_NOTE = (
    "Límite inferior sesgado: el núcleo satura SPHEREx y solo se miden los píxeles no saturados de la "
    "apertura. No sirve para afirmar variabilidad."
)

SCIENCE_NOTES: dict[str, ScienceNote] = {
    "WISE_0855": ScienceNote(
        science_note=WISE_0855_NOTE,
        quality="unvalidated",
        light_curve_note=(
            "Sin validar: el flujo está mezclado con una estrella fija vecina (a ~11″); no indica la "
            "variabilidad de WISE 0855."
        ),
    ),
    "BARNARDS_STAR": ScienceNote(
        science_note=(
            "El núcleo satura SPHEREx: no hay espectro ni curva de luz válidos de la estrella. Sí se ve su "
            "movimiento propio: entre la primera y la última visita su centroide se desplaza 10,3″ hacia el "
            "norte, frente a 10,2″ esperados."
        ),
        quality="low",
    ),
    "RW_AURIGAE": ScienceNote(
        science_note=(
            "El núcleo satura SPHEREx: el espectro y la curva de luz se miden solo con los píxeles no "
            "saturados de la apertura. Son límites inferiores sesgados y no sirven para afirmar variabilidad."
        ),
        quality="low",
    ),
    "R_DRACONIS": ScienceNote(
        science_note=(
            "El núcleo satura SPHEREx: no hay espectro ni fotometría válida del núcleo. El oscurecimiento del "
            "halo entre la primera y la última visita es real (cociente de flujo ~0,56) y coherente con una "
            "Mira en otra fase de su ciclo."
        ),
        quality="low",
        light_curve_note=(
            "El halo sí se oscurece entre la primera y la última visita (cociente ~0,56), coherente con una "
            "Mira en otra fase, pero el núcleo está saturado."
        ),
    ),
}
