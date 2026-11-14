"""Catalog of the 5 mandatory Golden Path targets.

Single source of truth shared by the API (lookup boxes) and the offline
pipeline (astrometry). `ra_deg`/`dec_deg` are the display coordinates
returned in `target_info`; the `ref_*` + proper-motion fields are what the
pipeline uses to find the star *at the SPHEREx observation date*.

Proper motion matters here: Barnard's Star moves ~10.4"/yr and WISE 0855
~8.1"/yr, i.e. several arcmin since J2000 - more than half of a 5' cutout.
Astrometry: Gaia DR3 (epoch 2016.0) where available. WISE 0855 is too faint
for Gaia; its reference position is its WISE designation (epoch ~2010.4)
with the Spitzer/WISE proper motion from Luhman & Esplin (2016).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Target:
    key: str
    name: str
    ra_deg: float
    dec_deg: float
    ref_ra_deg: float
    ref_dec_deg: float
    ref_epoch_yr: float
    pm_ra_cosdec_mas_yr: float = 0.0
    pm_dec_mas_yr: float = 0.0
    # API lookup box (inclusive, degrees). Defaults to +/-0.1 deg around ra/dec.
    ra_range: tuple[float, float] | None = None
    dec_range: tuple[float, float] | None = None

    @property
    def slug(self) -> str:
        """File-name stem for generated assets, e.g. 'wise0855'."""
        return self.key.lower().replace("_", "")

    @property
    def data_file(self) -> str:
        return f"{self.key.lower()}.json"

    def contains(self, ra: float, dec: float, half_width_deg: float = 0.1) -> bool:
        ra_lo, ra_hi = self.ra_range or (self.ra_deg - half_width_deg, self.ra_deg + half_width_deg)
        dec_lo, dec_hi = self.dec_range or (self.dec_deg - half_width_deg, self.dec_deg + half_width_deg)
        return ra_lo <= ra <= ra_hi and dec_lo <= dec <= dec_hi


CATALOG: dict[str, Target] = {
    t.key: t
    for t in (
        Target(
            key="WISE_0855",
            name="WISE 0855",
            ra_deg=133.795129,
            dec_deg=-7.245147,
            ref_ra_deg=133.795129,
            ref_dec_deg=-7.245147,
            ref_epoch_yr=2010.4,
            pm_ra_cosdec_mas_yr=-8118.0,
            pm_dec_mas_yr=673.0,
            ra_range=(133.7, 133.8),
            dec_range=(-7.3, -7.2),
        ),
        Target(
            key="BARNARDS_STAR",
            name="Barnard's Star",
            ra_deg=269.452075,
            dec_deg=4.693391,
            ref_ra_deg=269.44850252544,  # Gaia DR3 4472832130942575872
            ref_dec_deg=4.73942005111,
            ref_epoch_yr=2016.0,
            pm_ra_cosdec_mas_yr=-801.551,
            pm_dec_mas_yr=10362.394,
        ),
        Target(
            key="EPSILON_INDI_B",
            name="Epsilon Indi B",
            ra_deg=331.076667,
            dec_deg=-56.782500,
            ref_ra_deg=331.07645259656,  # Gaia DR3 6412596012146801152
            ref_dec_deg=-56.79381206688,
            ref_epoch_yr=2016.0,
            pm_ra_cosdec_mas_yr=3981.977,
            pm_dec_mas_yr=-2466.832,
        ),
        Target(
            key="RW_AURIGAE",
            name="RW Aurigae",
            ra_deg=76.956542,
            dec_deg=30.401444,
            ref_ra_deg=76.956542,
            ref_dec_deg=30.401444,
            ref_epoch_yr=2000.0,
            pm_ra_cosdec_mas_yr=2.641,  # Gaia DR3, negligible
            pm_dec_mas_yr=-27.398,
        ),
        Target(
            key="R_DRACONIS",
            name="R Draconis",
            ra_deg=248.165000,
            dec_deg=66.755000,
            ref_ra_deg=248.165000,
            ref_dec_deg=66.755000,
            ref_epoch_yr=2000.0,
            pm_ra_cosdec_mas_yr=-20.643,  # Gaia DR3, negligible
            pm_dec_mas_yr=6.149,
        ),
    )
}
