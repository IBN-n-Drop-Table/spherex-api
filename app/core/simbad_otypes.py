"""SIMBAD object-type groups for the `type_filter` parameter of /catalog-objects.

VERIFICATION STATUS (read before trusting these lists). Written 2026-11-15, while CDS
was reporting a security incident and its documentation pages (guide/otypes.htx) answered
404/403, so the official otype list could NOT be read. The codes below are from the
author's knowledge of SIMBAD's classification and are UNVERIFIED against the official list.
`scripts/generate_simbad_golden.py --check-otypes` compares them with the service's own
`otypedef` table (a table whose `otype` column was confirmed on the Harvard mirror's
VOSI /tables listing) and reports any code SIMBAD does not know. Run it once SIMBAD is back.

Design choices:

* `all`: no filter.
* `no_common_stars`: everything EXCEPT the most generic stellar types, so the overlay is not
  a wall of anonymous stars. It is an exclusion list on purpose: an unknown or rare type is
  shown rather than silently hidden (Barnard's Star, brown dwarfs and young stars stay in).
* `ir_galaxies`: galaxies and active nuclei, the extragalactic sources that SPHEREx sees in
  the infrared. An inclusion list. "IR" in the name is the audience (infrared mission), not a
  selection by infrared flux: that would need photometry, which this version does not filter on.
"""

from __future__ import annotations

import re
from typing import Literal

TypeFilter = Literal["all", "no_common_stars", "ir_galaxies"]

# Plain star, double/multiple star and the generic "in a binary/cluster" star flags.
COMMON_STAR_OTYPES: tuple[str, ...] = ("*", "**", "*iC", "*iN", "*iA", "*i*")

# Galaxies (and their active nuclei) as individual objects. Pairs, groups and clusters of
# galaxies are deliberately not included: one marker would cover several objects.
IR_GALAXY_OTYPES: tuple[str, ...] = (
    "G", "LSB", "bCG", "SBG", "H2G", "EmG", "rG", "IG", "PaG",
    "AGN", "LIN", "SyG", "Sy1", "Sy2", "Bla", "BLL", "QSO",
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
