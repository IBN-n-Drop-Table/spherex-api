"""Distances and plain-language texts for the 5 challenge targets (GET /targets).

Static configuration, deliberately NOT stored in the Golden Path JSON files.

Every distance comes from a trigonometric parallax with a citable source, and
the derived numbers (distance, light-travel time) are computed from it, never
typed by hand. Parallaxes were checked on 2026-11-14:

* Gaia DR3 (Gaia Collaboration, Vallenari et al. 2023, A&A 674, A1), queried
  from the ESA Gaia archive TAP service (gaiadr3.gaia_source) by source_id.
* WISE 0855 is too faint for Gaia: Spitzer parallax from Kirkpatrick et al.
  2021, ApJS 253, 7 (arXiv:2011.11616), ancillary Table 14 (439.0 +/- 2.4 mas,
  Teff 250 +/- 75 K).

`distance_uncertain` marks the two objects whose parallax is less reliable:
R Draconis (a pulsating Mira giant whose photocentre wanders; Gaia RUWE 1.30)
and RW Aurigae (a young binary: RW Aur A has Gaia RUWE 16.4, so we use the
cleaner parallax of its companion RW Aur B). Their texts say "roughly".

The texts are user-facing, so they are in English, although the API fields
keep their historical names (`description_es`, `story_es`).
"""

from __future__ import annotations

from dataclasses import dataclass

# 1 parsec in light years (IAU 2015: 1 pc = 648000/pi au; 1 ly = c * 365.25 d).
LY_PER_PC = 3.261563777
GAIA_DR3 = "Gaia DR3 (Gaia Collaboration, Vallenari et al. 2023, A&A 674, A1)"


@dataclass(frozen=True)
class TargetStory:
    parallax_mas: float
    parallax_error_mas: float
    distance_source: str
    distance_uncertain: bool
    description_es: str
    # Template for story_es; {years} is replaced with the rounded light-travel time.
    story_template_es: str

    @property
    def distance_ly(self) -> float:
        return round(1000.0 / self.parallax_mas * LY_PER_PC, 2)

    @property
    def light_left_years_ago(self) -> int:
        """Light-travel time, rounded: to the year when the distance is solid, to two
        significant figures when it is uncertain (no false precision)."""
        years = self.distance_ly
        if self.distance_uncertain:
            return int(round(years, -(len(str(int(years))) - 2)))
        return int(round(years))

    @property
    def story_es(self) -> str:
        return self.story_template_es.format(years=f"{self.light_left_years_ago:,}")  # 2,600


STORIES: dict[str, TargetStory] = {
    "BARNARDS_STAR": TargetStory(
        parallax_mas=546.975939730948,
        parallax_error_mas=0.040116355,
        distance_source=f"{GAIA_DR3}, source_id 4472832130942575872: parallax 546.976 ± 0.040 mas",
        distance_uncertain=False,
        description_es=(
            "A small, cool red dwarf, one of the closest stars to the Sun. "
            "It is the star that moves fastest across our sky."
        ),
        story_template_es="The light you see left this star about {years} years ago.",
    ),
    "EPSILON_INDI_B": TargetStory(
        parallax_mas=270.6580324694526,
        parallax_error_mas=0.6895573,
        distance_source=f"{GAIA_DR3}, source_id 6412596012146801152: parallax 270.66 ± 0.69 mas",
        distance_uncertain=False,
        description_es=(
            "A pair of brown dwarfs: objects bigger than Jupiter but too small to ignite "
            "like a star. They orbit the star Epsilon Indi A."
        ),
        story_template_es="The light you see left these brown dwarfs about {years} years ago.",
    ),
    "WISE_0855": TargetStory(
        parallax_mas=439.0,
        parallax_error_mas=2.4,
        distance_source=(
            "Kirkpatrick et al. 2021, ApJS 253, 7 (arXiv:2011.11616), Table 14: "
            "Spitzer parallax 439.0 ± 2.4 mas (not in Gaia: too faint in visible light)"
        ),
        distance_uncertain=False,
        description_es=(
            "One of the coldest brown dwarfs known: about 250 kelvin, roughly "
            "23 degrees below zero Celsius. It gives off almost no visible light; "
            "it was discovered thanks to infrared."
        ),
        story_template_es="The light you see left this brown dwarf about {years} years ago.",
    ),
    "RW_AURIGAE": TargetStory(
        parallax_mas=6.406249329219477,
        parallax_error_mas=0.04412279,
        distance_source=(
            f"{GAIA_DR3}, source_id 156430822114424576 (RW Aur B): parallax 6.406 ± 0.044 mas. "
            "RW Aur A (source_id 156430817820015232) has RUWE 16.4 and parallax 5.45 ± 0.33 mas, "
            "so the companion's parallax is used for the system"
        ),
        distance_uncertain=True,
        description_es=(
            "A very young star, still surrounded by the disk of gas and dust where "
            "planets form. Its brightness changes irregularly."
        ),
        story_template_es=(
            "The light you see left here roughly {years} years ago "
            "(the distance to this young system is hard to measure precisely)."
        ),
    ),
    "R_DRACONIS": TargetStory(
        parallax_mas=1.2509180143740992,
        parallax_error_mas=0.0647879,
        distance_source=(
            f"{GAIA_DR3}, source_id 1648328016354496384: parallax 1.251 ± 0.065 mas "
            "(Mira variable: large, pulsating star, so the parallax is less reliable)"
        ),
        distance_uncertain=True,
        description_es=(
            "An old red giant star that swells and shrinks: its brightness rises and "
            "falls over a cycle of several months."
        ),
        story_template_es=(
            "The light you see left this star roughly {years} years ago "
            "(pulsating giants like this one are hard to measure)."
        ),
    ),
}


def story_fields(target_key: str) -> dict[str, object]:
    """The optional /targets fields for one target ({} when there is no story)."""
    s = STORIES.get(target_key)
    if s is None:
        return {}
    return {
        "distance_ly": s.distance_ly,
        "distance_source": s.distance_source,
        "light_left_years_ago": s.light_left_years_ago,
        "distance_uncertain": s.distance_uncertain,
        "description_es": s.description_es,
        "story_es": s.story_es,
    }
