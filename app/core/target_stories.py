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
cleaner parallax of its companion RW Aur B). Their texts say "aproximadamente".
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
        return self.story_template_es.format(years=_spanish_int(self.light_left_years_ago))


def _spanish_int(n: int) -> str:
    """RAE: no separator up to 4 digits (2600), a thin space from 5 digits on (12 500)."""
    return str(n) if n < 10_000 else f"{n:,}".replace(",", " ")


STORIES: dict[str, TargetStory] = {
    "BARNARDS_STAR": TargetStory(
        parallax_mas=546.975939730948,
        parallax_error_mas=0.040116355,
        distance_source=f"{GAIA_DR3}, source_id 4472832130942575872: parallax 546.976 ± 0.040 mas",
        distance_uncertain=False,
        description_es=(
            "Una estrella enana roja, pequeña y fría, de las más cercanas al Sol. "
            "Es la estrella que más rápido se desplaza en nuestro cielo."
        ),
        story_template_es="La luz que ves salió de esta estrella hace unos {years} años.",
    ),
    "EPSILON_INDI_B": TargetStory(
        parallax_mas=270.6580324694526,
        parallax_error_mas=0.6895573,
        distance_source=f"{GAIA_DR3}, source_id 6412596012146801152: parallax 270.66 ± 0.69 mas",
        distance_uncertain=False,
        description_es=(
            "Una pareja de enanas marrones: objetos más grandes que Júpiter pero demasiado "
            "pequeños para encenderse como una estrella. Giran alrededor de la estrella Epsilon Indi A."
        ),
        story_template_es="La luz que ves salió de estas enanas marrones hace unos {years} años.",
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
            "Una de las enanas marrones más frías que se conocen: unos 250 kelvin, "
            "es decir, alrededor de 23 grados bajo cero. Casi no emite luz visible; "
            "se descubrió gracias al infrarrojo."
        ),
        story_template_es="La luz que ves salió de esta enana marrón hace unos {years} años.",
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
            "Una estrella muy joven, todavía rodeada del disco de gas y polvo en el que se "
            "forman los planetas. Su brillo cambia de forma irregular."
        ),
        story_template_es=(
            "La luz que ves salió de aquí hace aproximadamente {years} años "
            "(la distancia de este sistema joven es difícil de medir con precisión)."
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
            "Una estrella gigante roja y vieja que se hincha y se encoge: su brillo sube y "
            "baja en un ciclo de varios meses."
        ),
        story_template_es=(
            "La luz que ves salió de esta estrella hace aproximadamente {years} años "
            "(las gigantes que pulsan, como esta, son difíciles de medir)."
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
