"""Pieces shared by the routers that can fall back to a live job."""

from collections.abc import Callable, Hashable
from typing import Annotated
from uuid import UUID

from fastapi import BackgroundTasks, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.schemas.jobs import JobKind, JobResponse, JobStatus
from app.schemas.sky_patch import ErrorResponse
from app.services.jobs import JobService, TooManyJobs, get_job_service
from app.services.live import LiveRunner, get_live_runner

POLL_AFTER_S = 5

FovQuery = Annotated[
    float,
    Query(
        ge=1,
        le=30,
        description="Field of view in **arcminutes** (patch edge length). Cached Golden Path "
        "targets are always served at their own fov (5 arcmin).",
        examples=[5.0],
    ),
]
JobsDep = Annotated[JobService, Depends(get_job_service)]
RunnerDep = Annotated[LiveRunner, Depends(get_live_runner)]

LIVE_JOB_RESPONSES: dict[int | str, dict] = {
    status.HTTP_202_ACCEPTED: {
        "model": JobResponse,
        "description": "Not cached: a live job was queued. Poll the `Location` URL "
        "(GET /api/v1/jobs/{job_id}).",
    },
    status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse, "description": "Live job queue full"},
}


def submit_live_job(
    *,
    kind: JobKind,
    key: Hashable,
    work: Callable[[UUID], BaseModel],
    jobs: JobService,
    background_tasks: BackgroundTasks,
    request: Request,
) -> JSONResponse:
    """202 with a job (new or already running). A finished identical job
    answers 200 with its result straight away."""
    try:
        job, created = jobs.find_or_create(kind, key)
    except TooManyJobs as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Too many live queries in progress ({exc}). Try again shortly.",
            headers={"Retry-After": "30"},
        ) from exc

    if job.status is JobStatus.COMPLETED and job.result is not None:
        return JSONResponse(job.result.model_dump(mode="json"))
    if created:
        background_tasks.add_task(jobs.run, job.job_id, lambda: work(job.job_id))
    return JSONResponse(
        job.model_dump(mode="json"),
        status_code=status.HTTP_202_ACCEPTED,
        headers={
            "Location": str(request.url_for("get_job", job_id=job.job_id)),
            "Retry-After": str(POLL_AFTER_S),
        },
    )


def coordinate_key(ra: float, dec: float, fov: float) -> tuple:
    """Same position to ~0.04 arcsec + same fov -> same job."""
    return ("coords", round(ra % 360.0, 5), round(dec, 5), round(fov, 3))

