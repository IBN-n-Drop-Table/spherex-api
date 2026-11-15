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
    science_note: str  # shown with the sky patch and the target spectrum (English)
    quality: Quality | None = None  # response-level quality; None = not flagged here
    light_curve_note: str | None = None  # added to every light-curve point


WISE_0855_NOTE = (
    "Unvalidated data. In the broadband images WISE 0855 is blended with a neighboring fixed star "
    "(~11″ away) that dominates at 1 µm: the spectrum and light curve of this patch must not be "
    "read as a detection or as the spectrum of a Y dwarf. Its motion is confirmed only in "
    "detector 6 (4.4–5 µm): the centroid moves 7.9″ west between the first and the last visit, "
    "against 8.05″ expected. What can be said is that the patch follows its proper motion: it is "
    "centered on its position at the first visit, ~2′ west of its 2010 catalog position."
)

# Any response with `saturated: true` (cached or live) gets this on every light-curve
# point that has a flux: photometry used only the unsaturated pixels of the aperture.
SATURATED_LIGHT_CURVE_NOTE = (
    "Biased lower limit: the core saturates SPHEREx and only the unsaturated pixels of the "
    "aperture are measured. It cannot be used to claim variability."
)

SCIENCE_NOTES: dict[str, ScienceNote] = {
    "WISE_0855": ScienceNote(
        science_note=WISE_0855_NOTE,
        quality="unvalidated",
        light_curve_note=(
            "Unvalidated: the flux is blended with a neighboring fixed star (~11″ away); it does not "
            "show the variability of WISE 0855."
        ),
    ),
    "BARNARDS_STAR": ScienceNote(
        science_note=(
            "The core saturates SPHEREx: there is no valid spectrum or light curve of the star. Its "
            "proper motion does show: between the first and the last visit its centroid moves 10.3″ "
            "north, against 10.2″ expected."
        ),
        quality="low",
    ),
    "RW_AURIGAE": ScienceNote(
        science_note=(
            "The core saturates SPHEREx: the spectrum and the light curve are measured only with the "
            "unsaturated pixels of the aperture. They are biased lower limits and cannot be used to "
            "claim variability."
        ),
        quality="low",
    ),
    "R_DRACONIS": ScienceNote(
        science_note=(
            "The core saturates SPHEREx: there is no valid spectrum or photometry of the core. The "
            "dimming of the halo between the first and the last visit is real (flux ratio ~0.56) and "
            "consistent with a Mira at another phase of its cycle."
        ),
        quality="low",
        light_curve_note=(
            "The halo does dim between the first and the last visit (ratio ~0.56), consistent with a "
            "Mira at another phase, but the core is saturated."
        ),
    ),
}
