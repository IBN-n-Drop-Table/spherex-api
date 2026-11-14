from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.schemas.sky_patch import ErrorResponse, SkyPatchResponse
from app.services.golden_path import find_sky_patch

router = APIRouter(tags=["sky-patch"])

NOT_FOUND_DETAIL = "Target not found in Golden Path cache. Try WISE_0855."


@router.get(
    "/sky-patch",
    response_model=SkyPatchResponse,
    summary="Get a cached sky patch (timelapse, difference image, spectrum, light curve)",
    responses={status.HTTP_404_NOT_FOUND: {"model": ErrorResponse}},
)
def get_sky_patch(
    target: Annotated[
        str | None, Query(description="Target id, e.g. WISE_0855", examples=["WISE_0855"])
    ] = None,
    ra: Annotated[float | None, Query(ge=0, lt=360, description="Right ascension (deg)")] = None,
    dec: Annotated[float | None, Query(ge=-90, le=90, description="Declination (deg)")] = None,
    fov: Annotated[float | None, Query(gt=0, description="Field of view (arcmin)")] = None,
) -> SkyPatchResponse:
    # `fov` is accepted for forward compatibility; the Golden Path cache
    # serves a fixed field of view per target for now.
    patch = find_sky_patch(target=target, ra=ra, dec=dec)
    if patch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND_DETAIL)
    return patch
