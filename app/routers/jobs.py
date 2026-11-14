from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response, status

from app.routers.common import (
    POLL_AFTER_S,
    JobsDep,
    RunnerDep,
    coordinate_key,
    job_reply,
    parse_coords,
    render_job,
    submit_live_job,
)
from app.schemas.jobs import JobCreate, JobKind, JobResponse, JobStatus
from app.schemas.sky_patch import ErrorResponse
from app.services.fallback import sky_patch_fallback, spectrum_fallback
from app.services.golden_path import catalog_target, find_cached_spectrum, find_sky_patch
from app.services.live import coordinate_target

router = APIRouter(tags=["jobs"])

JOB_NOT_FOUND = "Job not found (unknown id, or expired)."
CACHED_MESSAGE = "Datos precalculados (Golden Path): respuesta instantánea."


@router.post(
    "/jobs",
    response_model=JobResponse,
    summary="Start (or reuse) a sky-patch / spectrum computation and get a job_id at once",
    description="Cached Golden Path answers come back **200** with `status: completed` and the "
    "result. Anything else answers **202** with a job; poll GET /api/v1/jobs/{job_id} every "
    "`retry_after_ms` and show `message` / `progress`. The same query returns the same job. "
    "A cold computation takes tens of seconds to a few minutes (IRSA cutouts take ~12 s each).",
    responses={
        status.HTTP_202_ACCEPTED: {"model": JobResponse, "description": "Queued or running"},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
    },
)
def create_job(body: JobCreate, request: Request, background_tasks: BackgroundTasks,
               jobs: JobsDep, runner: RunnerDep):
    ra_deg, dec_deg = parse_coords(body.ra, body.dec)
    if body.target is None and ra_deg is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail="Give `target` or both `ra` and `dec`.")

    if body.kind is JobKind.SKY_PATCH:
        cached = find_sky_patch(target=body.target, ra=ra_deg, dec=dec_deg)
    else:
        cached = find_cached_spectrum(body.target, None, ra_deg, dec_deg)
    if cached is not None:
        key = ("cached", body.target, ra_deg, dec_deg)
        job = jobs.add_completed(body.kind, key, cached, CACHED_MESSAGE)
        return job_reply(job, request, body.band)

    live_target = catalog_target(body.target) if body.target else None
    if live_target is not None:
        key = ("target", live_target.key, round(body.fov, 3))
    elif ra_deg is not None:
        live_target, key = coordinate_target(ra_deg, dec_deg), coordinate_key(ra_deg, dec_deg, body.fov)
    else:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown target {body.target!r}.")

    if body.kind is JobKind.SKY_PATCH:
        work = lambda job_id, progress: runner.sky_patch(live_target, body.fov, job_id, progress)  # noqa: E731
        fallback = lambda reason: sky_patch_fallback(live_target.ra_deg, live_target.dec_deg, reason)  # noqa: E731
    else:
        work = lambda job_id, progress: runner.spectrum(live_target, body.fov, job_id, progress)  # noqa: E731
        fallback = lambda reason: spectrum_fallback(live_target.ra_deg, live_target.dec_deg, reason)  # noqa: E731
    job, _ = submit_live_job(kind=body.kind, key=key, work=work, fallback=fallback, jobs=jobs,
                             background_tasks=background_tasks)
    return job_reply(job, request, body.band)


@router.get(
    "/jobs/{job_id}",
    name="get_job",
    response_model=JobResponse,
    summary="Poll a job: status, stage, progress, message, result",
    responses={status.HTTP_404_NOT_FOUND: {"model": ErrorResponse}},
)
def get_job(job_id: UUID, jobs: JobsDep, request: Request, response: Response):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=JOB_NOT_FOUND)
    if job.status in (JobStatus.QUEUED, JobStatus.RUNNING):
        response.headers["Retry-After"] = str(POLL_AFTER_S)
    return render_job(job, request)
