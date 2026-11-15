from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.core.config import settings
from app.core.simbad_otypes import TypeFilter
from app.core.simbad_settings import SimbadSettings
from app.core.sky_projection import years_since_j2000
from app.routers.common import DecQuery, FovQuery, RaQuery, parse_coords
from app.schemas.catalog_objects import CatalogObjectsResponse
from app.schemas.sky_patch import ErrorResponse
from app.services.catalog_objects import UnknownEpoch, build_response, golden_target_key, image_geometry
from app.services.golden_path import catalog_target
from app.services.rate_limit import SlidingWindowLimiter, enforce
from app.services.simbad import SimbadService, get_simbad_service

router = APIRouter(tags=["catalog-objects"])

MAX_N_CAP = 50


@lru_cache
def get_simbad_limiter() -> SlidingWindowLimiter:
    return SlidingWindowLimiter(SimbadSettings().rate_per_min, 60.0)


@router.get(
    "/catalog-objects",
    response_model=CatalogObjectsResponse,
    summary="Objects catalogued in SIMBAD, as pixels on the sky-patch image",
    description="Cone search in SIMBAD around the image centre (radius = half the diagonal), positions moved to "
    "the image's observation date with SIMBAD's proper motion, projected with the WCS of the PNG that "
    "/sky-patch serves. Most-cited first. **Label the markers as catalogue entries, not SPHEREx detections** "
    "(`notice`). Always 200: if SIMBAD fails, a saved result is served with `fallback_reason`.",
    responses={status.HTTP_404_NOT_FOUND: {"model": ErrorResponse}, status.HTTP_429_TOO_MANY_REQUESTS: {"model": ErrorResponse}},
)
def get_catalog_objects(
    request: Request,
    limiter: Annotated[SlidingWindowLimiter, Depends(get_simbad_limiter)],
    service: Annotated[SimbadService, Depends(get_simbad_service)],
    target: Annotated[str | None, Query(description="Target id, as in /sky-patch", examples=["BARNARDS_STAR"])] = None,
    ra: RaQuery = None,
    dec: DecQuery = None,
    fov: FovQuery = 5.0,
    epoch: Annotated[str | None, Query(description="Golden Path visit id (e.g. 2025_M7) to move the positions to. "
                                       "Default: the first visit", examples=["2025_M0"])] = None,
    max_n: Annotated[int, Query(ge=1, le=MAX_N_CAP, description="Objects returned (1-50)")] = 20,
    type_filter: Annotated[TypeFilter, Query(description="`all`; `no_common_stars` hides plain and multiple stars; "
                                             "`ir_galaxies` keeps galaxies and active nuclei")] = "all",
):
    enforce(limiter, request, "catalog-objects")
    ra_deg, dec_deg = parse_coords(ra, dec)
    golden_key = golden_target_key(target, ra_deg, dec_deg)
    if golden_key is None:
        if ra_deg is None:
            if target and catalog_target(target) is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown target. Try WISE_0855, or give ra and dec.")
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Give a target, or ra and dec.")
        if fov > settings.live_max_fov_arcmin:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                                f"fov: at most {settings.live_max_fov_arcmin:g} arcmin outside the Golden Path targets.")
    try:
        geo = image_geometry(golden_key, ra_deg, dec_deg, fov, epoch)
    except UnknownEpoch as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    ra0, dec0 = geo.center
    found = service.lookup(ra0, dec0, geo.radius_deg, type_filter, years_since_j2000(geo.when), golden_key)
    return build_response(found, geo, max_n, service.cfg.fetch_limit)
