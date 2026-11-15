"""SIMBAD object-type groups for the `type_filter` parameter of /catalog-objects.

VERIFIED 2026-11-15: every code below exists in SIMBAD's `otypedef` table, read from the live service
(through the Harvard mirror, because CDS's own documentation pages were down): tests/simbad_otypes_known.txt
holds the 226 official codes and tests/test_catalog_objects.py compares. The hierarchy (`otypedef.path`)
was used to choose the groups. `scripts/generate_simbad_golden.py --check-otypes` repeats the check.
What is a judgement call, not a fact, is WHICH codes belong to a group.

* `all`: no filter.
* `no_common_stars`: everything EXCEPT generic stellar types, so the overlay is not a wall of anonymous
  stars. An exclusion list on purpose: a rare or unknown type is shown rather than silently hidden
  (Barnard's Star is `BY*`, brown dwarfs `BD*`, young stars `TT*`/`Or*`: all stay in).
  Excluded: `*` Star, `**` Double or Multiple Star, `MS*` Main Sequence Star, `V*` Variable Star,
  `SB*` Spectroscopic Binary.
* `ir_galaxies`: individual galaxies and active nuclei, the extragalactic sources SPHEREx sees in the
  infrared. An inclusion list of confirmed (not `?` candidate) types under `G` in the hierarchy. "IR" is
  the audience (an infrared mission), not a selection by infrared flux: that would need photometry,
  which this version does not filter on. Pairs, groups and clusters of galaxies are left out (one marker
  would stand for several objects).

Known quirk: `Pl` (exoplanet) rows share the position of their host star, so on a system like Barnard's
Star several markers land on the same pixel. They are in `all` and `no_common_stars` as SIMBAD has them.
"""

from __future__ import annotations

import re
from typing import Literal

TypeFilter = Literal["all", "no_common_stars", "ir_galaxies"]

# Generic stellar types: plain, multiple, main-sequence, generic variable, spectroscopic binary.
COMMON_STAR_OTYPES: tuple[str, ...] = ("*", "**", "MS*", "V*", "SB*")

# Individual galaxies and active nuclei (all under `G` in SIMBAD's hierarchy).
IR_GALAXY_OTYPES: tuple[str, ...] = (
    "G", "GiC", "BiC", "GiG", "GiP", "LSB", "bCG", "SBG", "H2G", "EmG", "rG",
    "AGN", "LIN", "SyG", "Sy1", "Sy2", "QSO", "Bla", "BLL",
)

_CODE = re.compile(r"^[A-Za-z0-9*?+>_-]+$")  # codes go into ADQL: nothing else may pass
assert all(_CODE.match(c) for c in COMMON_STAR_OTYPES + IR_GALAXY_OTYPES)


def adql_condition(type_filter: TypeFilter, column: str = "b.otype") -> str | None:
    """The ADQL fragment (without leading AND) that implements a type filter, or None."""
    if type_filter == "all":
        return None
    if type_filter == "no_common_stars":
        return f"{column} NOT IN ({_quoted(COMMON_STAR_OTYPES)})"
    if type_filter == "ir_galaxies":
        return f"{column} IN ({_quoted(IR_GALAXY_OTYPES)})"
    raise ValueError(f"unknown type_filter: {type_filter!r}")


def matches(type_filter: TypeFilter, otype: str) -> bool:
    """The same rule applied in Python (cache and precomputed rows are stored unfiltered)."""
    if type_filter == "all":
        return True
    if type_filter == "no_common_stars":
        return otype not in COMMON_STAR_OTYPES
    if type_filter == "ir_galaxies":
        return otype in IR_GALAXY_OTYPES
    raise ValueError(f"unknown type_filter: {type_filter!r}")


def _quoted(codes: tuple[str, ...]) -> str:
    return ", ".join("'" + c + "'" for c in codes)
