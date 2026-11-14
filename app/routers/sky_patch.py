from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse

from app.routers.common import (
    LIVE_JOB_RESPONSES,
    FovQuery,
    JobsDep,
    RunnerDep,
    coordinate_key,
    submit_live_job,
)
from app.schemas.jobs import JobKind
from app.schemas.sky_patch import ErrorResponse, SkyPatchResponse
from app.services.golden_path import catalog_target, find_sky_patch
from app.services.live import coordinate_target

router = APIRouter(tags=["sky-patch"])

NOT_FOUND_DETAIL = "Target not found in Golden Path cache. Try WISE_0855."


@router.get(
    "/sky-patch",
    response_model=SkyPatchResponse,
    summary="Sky patch: timelapse, difference image, spectra, light curve",
    description="Cached Golden Path targets answer **200** at once. Other catalogue targets "
    "and other coordinates answer **202** with a job to poll at GET /api/v1/jobs/{job_id}.",
    responses={status.HTTP_404_NOT_FOUND: {"model": ErrorResponse}, **LIVE_JOB_RESPONSES},
)
def get_sky_patch(
    request: Request,
    background_tasks: BackgroundTasks,
    jobs: JobsDep,
    runner: RunnerDep,
    target: Annotated[
        str | None, Query(description="Target id, e.g. WISE_0855", examples=["WISE_0855"])
    ] = None,
    ra: Annotated[float | None, Query(ge=0, lt=360, description="Right ascension (deg)")] = None,
    dec: Annotated[float | None, Query(ge=-90, le=90, description="Declination (deg)")] = None,
    fov: FovQuery = 5.0,
) -> SkyPatchResponse | JSONResponse:
    patch = find_sky_patch(target=target, ra=ra, dec=dec)
    if patch is not None:
        return patch

    live_target = catalog_target(target) if target else None
    if live_target is not None:
        key = ("target", live_target.key, round(fov, 3))
    elif ra is not None and dec is not None:
        live_target, key = coordinate_target(ra, dec), coordinate_key(ra, dec, fov)
    else:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND_DETAIL)

    base_url = str(request.base_url)
    return submit_live_job(
        kind=JobKind.SKY_PATCH,
        key=key,
        work=lambda job_id: runner.sky_patch(live_target, fov, base_url, job_id),
        jobs=jobs,
        background_tasks=background_tasks,
        request=request,
    )
