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
from app.schemas.sky_patch import ErrorResponse
from app.schemas.spectrum import SpectrumResponse
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
    ra: Annotated[float | None, Query(ge=0, lt=360, description="Right ascension (deg)")] = None,
    dec: Annotated[float | None, Query(ge=-90, le=90, description="Declination (deg)")] = None,
    radius_arcsec: Annotated[
        float, Query(gt=0, le=60, description="Match radius for ra/dec (SPHEREx px = 6.15\")")
    ] = 6.0,
    fov: FovQuery = 5.0,
) -> SpectrumResponse | JSONResponse:
    has_coords = ra is not None and dec is not None
    if target is None and not has_coords:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Give `target` or both `ra` and `dec`."
        )
    try:
        spectrum = find_cached_spectrum(target, anomaly_id, ra, dec, radius_arcsec)
    except AnomalyNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if spectrum is not None:
        return spectrum
    if anomaly_id is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="anomaly_id only exists for cached Golden Path targets.",
        )

    live_target = catalog_target(target) if target else None
    if live_target is not None:
        key = ("target", live_target.key, round(fov, 3))
    elif has_coords:
        live_target, key = coordinate_target(ra, dec), coordinate_key(ra, dec, fov)
    else:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown target {target!r}.")

    base_url = str(request.base_url)
    return submit_live_job(
        kind=JobKind.SPECTRUM,
        key=key,
        work=lambda job_id: runner.spectrum(live_target, fov, base_url, job_id),
        jobs=jobs,
        background_tasks=background_tasks,
        request=request,
    )
