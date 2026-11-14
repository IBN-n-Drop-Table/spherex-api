from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, status

from app.routers.common import (
    LIVE_JOB_RESPONSES,
    BandQuery,
    DecQuery,
    FovQuery,
    JobsDep,
    RaQuery,
    RunnerDep,
    coordinate_key,
    job_reply,
    parse_coords,
    render,
    submit_live_job,
)
from app.schemas.jobs import JobKind
from app.schemas.sky_patch import ErrorResponse, SkyPatchResponse
from app.services.fallback import sky_patch_fallback
from app.services.golden_path import catalog_target, find_sky_patch
from app.services.live import coordinate_target

router = APIRouter(tags=["sky-patch"])

NOT_FOUND_DETAIL = "Target not found in Golden Path cache. Try WISE_0855."


@router.get(
    "/sky-patch",
    response_model=SkyPatchResponse,
    summary="Sky patch: timelapse, difference image, spectra, light curve",
    description="Cached Golden Path targets answer **200** at once. Other catalogue targets "
    "and other coordinates answer **202** with a job to poll at GET /api/v1/jobs/{job_id} "
    "(or use POST /api/v1/jobs). RA/Dec accept degrees or sexagesimal. If IRSA is slow or "
    "down, the job completes with cached data and `fallback: true`.",
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
    ra: RaQuery = None,
    dec: DecQuery = None,
    fov: FovQuery = 5.0,
    band: BandQuery = None,
):
    ra_deg, dec_deg = parse_coords(ra, dec)
    patch = find_sky_patch(target=target, ra=ra_deg, dec=dec_deg)
    if patch is not None:
        return render(patch, request, band)

    live_target = catalog_target(target) if target else None
    if live_target is not None:
        key = ("target", live_target.key, round(fov, 3))
    elif ra_deg is not None:
        live_target, key = coordinate_target(ra_deg, dec_deg), coordinate_key(ra_deg, dec_deg, fov)
    else:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND_DETAIL)

    job, _ = submit_live_job(
        kind=JobKind.SKY_PATCH,
        key=key,
        work=lambda job_id, progress: runner.sky_patch(live_target, fov, job_id, progress),
        fallback=lambda reason: sky_patch_fallback(live_target.ra_deg, live_target.dec_deg, reason),
        jobs=jobs,
        background_tasks=background_tasks,
    )
    return job_reply(job, request, band, completed_as_result=True)
