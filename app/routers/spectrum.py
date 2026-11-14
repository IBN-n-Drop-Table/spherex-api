from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, status
from app.routers.common import (
    LIVE_JOB_RESPONSES,
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
from app.schemas.sky_patch import ErrorResponse
from app.schemas.spectrum import SpectrumResponse
from app.services.fallback import spectrum_fallback
from app.services.golden_path import AnomalyNotFound, catalog_target, find_cached_spectrum
from app.services.live import coordinate_target

router = APIRouter(tags=["spectrum"])


@router.get(
    "/spectrum",
    response_model=SpectrumResponse,
    summary="102-band SPHEREx spectrum of a target, an anomaly, or any position",
    description="Precomputed spectra (cached target centres and every detected anomaly) "
    "answer **200**. Pick one with `target` (+ `anomaly_id`), or with `ra`/`dec` within "
    "`radius_arcsec`. Anything else answers **202** with a job to poll.",
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorResponse},
        **LIVE_JOB_RESPONSES,
    },
)
def get_spectrum(
    request: Request,
    background_tasks: BackgroundTasks,
    jobs: JobsDep,
    runner: RunnerDep,
    target: Annotated[str | None, Query(examples=["WISE_0855"])] = None,
    anomaly_id: Annotated[str | None, Query(examples=["anom_04"])] = None,
    ra: RaQuery = None,
    dec: DecQuery = None,
    radius_arcsec: Annotated[
        float, Query(gt=0, le=60, description="Match radius for ra/dec (SPHEREx px = 6.15\")")
    ] = 6.0,
    fov: FovQuery = 5.0,
):
    ra_deg, dec_deg = parse_coords(ra, dec)
    has_coords = ra_deg is not None
    if target is None and not has_coords:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Give `target` or both `ra` and `dec`."
        )
    try:
        spectrum = find_cached_spectrum(target, anomaly_id, ra_deg, dec_deg, radius_arcsec)
    except AnomalyNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if spectrum is not None:
        return render(spectrum, request)
    if anomaly_id is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="anomaly_id only exists for cached Golden Path targets.",
        )

    live_target = catalog_target(target) if target else None
    if live_target is not None:
        key = ("target", live_target.key, round(fov, 3))
    elif has_coords:
        live_target, key = coordinate_target(ra_deg, dec_deg), coordinate_key(ra_deg, dec_deg, fov)
    else:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown target {target!r}.")

    job, _ = submit_live_job(
        kind=JobKind.SPECTRUM,
        key=key,
        work=lambda job_id, progress: runner.spectrum(live_target, fov, job_id, progress),
        fallback=lambda reason: spectrum_fallback(live_target.ra_deg, live_target.dec_deg, reason),
        jobs=jobs,
        background_tasks=background_tasks,
    )
    return job_reply(job, request, completed_as_result=True)
