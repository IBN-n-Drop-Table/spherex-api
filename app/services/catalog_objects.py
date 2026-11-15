"""Catalogue objects on an image: which image, which epoch, propagation, projection, ranking."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote

from app.core.credits import DATA_CREDITS, SIMBAD_CREDIT
from app.core.patch_geometry import patch_wcs
from app.core.sky_projection import half_diagonal_deg, project, propagate, years_since_j2000
from app.core.targets import CATALOG
from app.services.game import julian_year
from app.schemas.catalog_objects import (
    NOTICE, CatalogObject, CatalogObjectsResponse, EpochApplied, ImageSize,
)
from app.services.golden_path import cached_target
from app.services.simbad import Lookup, SimbadRow

SIMBAD_ID_URL = "https://simbad.cds.unistra.fr/simbad/sim-id?Ident="
LIVE_PNG_PX = 600  # size of the PNGs the live pipeline writes (scripts/generate_golden_path.py --png-size)
CREDITS = [SIMBAD_CREDIT] + [c for c in DATA_CREDITS if c["id"] in ("spherex_dr1", "spherex_qr3")]


class UnknownEpoch(ValueError):
    pass


@dataclass(frozen=True)
class ImageGeometry:
    wcs: dict
    width: int
    height: int
    epoch_id: str | None
    when: datetime
    golden_key: str | None

    @property
    def center(self) -> tuple[float, float]:
        """Sky position of the middle of the image (the patch centre: crpix is the middle pixel)."""
        return self.wcs["crval1"], self.wcs["crval2"]

    @property
    def radius_deg(self) -> float:
        return half_diagonal_deg(self.wcs, self.width, self.height)


def golden_target_key(target: str | None, ra: float | None, dec: float | None) -> str | None:
    from app.services.golden_path import normalize_target
    if target:
        key = normalize_target(target)
        if key in CATALOG and cached_target(CATALOG[key]):
            return key
    if ra is not None and dec is not None:
        for key, t in CATALOG.items():
            if t.contains(ra, dec) and cached_target(t):
                return key
    return None


def image_geometry(golden_key: str | None, ra: float | None, dec: float | None, fov_arcmin: float,
                   epoch_id: str | None, now: datetime | None = None) -> ImageGeometry:
    """The image the user sees. Golden Path: the cached PNG's own WCS and the date of the chosen visit
    (default: the first visit, which is the one the WCS is centred on). Coordinates: the north-up TAN
    grid the live pipeline writes for that centre and fov, observed 'now' (the visit dates are unknown)."""
    if golden_key:
        patch = cached_target(CATALOG[golden_key])
        epochs = {e.epoch_id: e for e in patch.timelapse_epochs}
        chosen = epoch_id or patch.timelapse_epochs[0].epoch_id
        if chosen not in epochs:
            raise UnknownEpoch(f"Unknown epoch '{epoch_id}'. Available: {', '.join(epochs)}.")
        wcs = patch.patch.wcs.model_dump()
        return ImageGeometry(wcs, patch.patch.width_px, patch.patch.height_px, chosen,
                             epochs[chosen].date, golden_key)
    if epoch_id:
        raise UnknownEpoch("epoch is only available for Golden Path targets.")
    assert ra is not None and dec is not None
    return ImageGeometry(patch_wcs(ra, dec, fov_arcmin, LIVE_PNG_PX, LIVE_PNG_PX), LIVE_PNG_PX, LIVE_PNG_PX,
                         None, now or datetime.now(UTC), None)


def place(row: SimbadRow, geo: ImageGeometry) -> CatalogObject | None:
    """Propagate one row to the image date and project it; None when it falls outside the image."""
    moves = row.pmra is not None and row.pmdec is not None
    ra, dec = row.ra, row.dec
    if moves:
        ra, dec = propagate(ra, dec, row.pmra, row.pmdec, years_since_j2000(geo.when))
    xy = project(geo.wcs, geo.width, geo.height, ra, dec)
    if xy is None:
        return None
    return CatalogObject(
        id=row.main_id, otype=row.otype, otype_label=row.otype_label, x=round(xy[0], 2), y=round(xy[1], 2),
        mag=row.mag, mag_band=row.mag_band, nbref=row.nbref,
        simbad_url=SIMBAD_ID_URL + quote(row.main_id, safe=""), proper_motion_applied=moves,
    )


FALLBACK_TEXT = {
    ("simbad_unavailable", "cache"): "SIMBAD did not answer in time. Showing the last result saved for this field.",
    ("simbad_unavailable", "precomputed"): "SIMBAD did not answer in time. Showing a precomputed list for this target.",
    ("simbad_unavailable", "none"): "SIMBAD did not answer in time and no saved result exists for this field, "
                                    "so no catalogued objects are shown.",
    # simbad_rate_limited = THIS server is pacing its own queries to SIMBAD (SIMBAD did not refuse anything).
    ("simbad_rate_limited", "cache"): "We are slowing down our queries to SIMBAD for a moment. Showing the last "
                                      "result saved for this field.",
    ("simbad_rate_limited", "precomputed"): "We are slowing down our queries to SIMBAD for a moment. Showing a "
                                            "precomputed list for this target.",
    ("simbad_rate_limited", "none"): "We are slowing down our queries to SIMBAD for a moment and no saved result "
                                     "exists for this field yet, so no catalogued objects are shown. Try again in "
                                     "a few seconds.",
}


def build_response(lookup: Lookup, geo: ImageGeometry, max_n: int, fetch_limit: int) -> CatalogObjectsResponse:
    placed = [o for o in (place(r, geo) for r in lookup.rows) if o is not None]
    placed.sort(key=lambda o: (-o.nbref, o.id))  # relevance only; ties broken by id so the order is stable
    truncated = len(placed) > max_n or len(lookup.rows) >= fetch_limit
    reason = lookup.fallback_reason
    return CatalogObjectsResponse(
        objects=placed[:max_n],
        image_size=ImageSize(width=geo.width, height=geo.height),
        epoch_applied=EpochApplied(epoch_id=geo.epoch_id, date=geo.when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                   julian_year=round(julian_year(geo.when.astimezone(UTC)), 3)),
        truncated=truncated, source=lookup.source, fallback_reason=reason, data_credits=CREDITS,
        notice=NOTICE, fallback_message=FALLBACK_TEXT[(reason, lookup.source)] if reason else None,
    )
