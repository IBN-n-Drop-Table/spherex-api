"""Pieces shared by the routers: query types, public URLs, live jobs with fallback."""

import math
from collections.abc import Callable, Hashable
from typing import Annotated, Any
from uuid import UUID

from fastapi import BackgroundTasks, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.config import settings
from app.core.coords import CoordinateError, parse_dec, parse_ra
from app.core.urls import to_public
from app.schemas.jobs import JobKind, JobResponse, JobStatus
from app.schemas.sky_patch import ChemicalBand, ErrorResponse, SkyPatchResponse
from app.services.fallback import NoCachedData
from app.services.jobs import JobService, TooManyJobs, get_job_service
from app.services.live import LiveProcessingUnavailable, LiveRunner, UpstreamUnavailable, get_live_runner

POLL_AFTER_S = max(1, math.ceil(settings.retry_after_ms / 1000))

FovQuery = Annotated[
    float,
    Query(
        ge=1,
        le=30,
        description="Field of view in **arcminutes** (patch edge length). Cached Golden Path "
        "targets are always served at their own fov (5 arcmin). Live jobs are capped at "
        "LIVE_MAX_FOV_ARCMIN (default 15).",
        examples=[5.0],
    ),
]
RaQuery = Annotated[
    str | None,
    Query(description="Right ascension: degrees (`133.7951`) or sexagesimal hours (`08h55m10.831s`)",
          examples=["08h55m10.831s"]),
]
DecQuery = Annotated[
    str | None,
    Query(description="Declination: degrees (`-7.2451`) or sexagesimal (`-7d14m42.53s`)",
          examples=["-7d14m42.53s"]),
]
BandQuery = Annotated[
    ChemicalBand | None,
    Query(description="Chemical filter the UI shows. Echoed back as `band`; every filter is always returned"),
]
JobsDep = Annotated[JobService, Depends(get_job_service)]
RunnerDep = Annotated[LiveRunner, Depends(get_live_runner)]

LIVE_JOB_RESPONSES: dict[int | str, dict] = {
    status.HTTP_202_ACCEPTED: {
        "model": JobResponse,
        "description": "Not cached: a live job was queued. Poll the `Location` URL "
        "(GET /api/v1/jobs/{job_id}) every `retry_after_ms`.",
    },
    status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse, "description": "Live job queue full"},
}

ProgressFn = Callable[[str, float, str], None]


def parse_coords(ra: str | float | None, dec: str | float | None) -> tuple[float | None, float | None]:
    """(ra_deg, dec_deg), or (None, None) unless both are given. Bad input -> 422."""
    if ra is None or dec is None or ra == "" or dec == "":
        return None, None
    try:
        return parse_ra(ra), parse_dec(dec)
    except CoordinateError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc


def public_base(request: Request) -> str:
    return settings.public_base or str(request.base_url)


def render(model: BaseModel, request: Request, band: str | None = None) -> dict[str, Any]:
    """Model -> JSON dict with absolute image URLs (and `band` echoed on sky patches)."""
    if band is not None and isinstance(model, SkyPatchResponse):
        model = model.model_copy(update={"band": band})
    return to_public(model, public_base(request))


def render_job(job: JobResponse, request: Request, band: str | None = None) -> dict[str, Any]:
    if band is not None and isinstance(job.result, SkyPatchResponse):
        job = job.model_copy(update={"result": job.result.model_copy(update={"band": band})})
    return to_public(job, public_base(request))


def job_location(request: Request, job_id: UUID) -> str:
    url = str(request.url_for("get_job", job_id=job_id))
    if settings.public_base:
        url = settings.public_base.rstrip("/") + "/" + url.split("/", 3)[3]
    return url


def submit_live_job(
    *,
    kind: JobKind,
    key: Hashable,
    work: Callable[[UUID, ProgressFn], BaseModel],
    fallback: Callable[[Exception], BaseModel],
    jobs: JobService,
    background_tasks: BackgroundTasks,
) -> tuple[JobResponse, bool]:
    """Find or start the job for `key`. Returns (job, created).

    An IRSA outage (timeout, 429, 5xx) or a server without the science stack
    never fails the job: it completes with cached data and `fallback: true`.
    """
    try:
        job, created = jobs.find_or_create(kind, key)
    except TooManyJobs as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Too many live queries in progress ({exc}). Try again shortly.",
            headers={"Retry-After": "30"},
        ) from exc

    if created:
        job_id = job.job_id

        def run() -> BaseModel:
            def progress(stage: str, fraction: float, message: str) -> None:
                jobs.report(job_id, stage, fraction, message)

            try:
                return work(job_id, progress)
            except (UpstreamUnavailable, LiveProcessingUnavailable) as exc:
                try:
                    result = fallback(exc)
                except NoCachedData:
                    raise exc from None
                jobs.report(job_id, "done", 1.0, result.fallback_message or "Datos en caché")
                return result

        background_tasks.add_task(jobs.run, job_id, run)
    return job, created


def job_reply(job: JobResponse, request: Request, band: str | None = None,
              completed_as_result: bool = False) -> JSONResponse | dict[str, Any]:
    """202 + Location while running. Finished: the job (or, for the legacy GET
    endpoints, its bare result, as before)."""
    if job.status is JobStatus.COMPLETED and job.result is not None and completed_as_result:
        return render(job.result, request, band)
    if job.status in (JobStatus.QUEUED, JobStatus.RUNNING):
        return JSONResponse(
            render_job(job, request, band),
            status_code=status.HTTP_202_ACCEPTED,
            headers={"Location": job_location(request, job.job_id), "Retry-After": str(POLL_AFTER_S)},
        )
    return JSONResponse(render_job(job, request, band))


def coordinate_key(ra: float, dec: float, fov: float) -> tuple:
    """Same position to ~0.04 arcsec + same fov -> same job."""
    return ("coords", round(ra % 360.0, 5), round(dec, 5), round(fov, 3))
