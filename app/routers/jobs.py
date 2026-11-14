from uuid import UUID

from fastapi import APIRouter, HTTPException, Response, status

from app.routers.common import POLL_AFTER_S, JobsDep
from app.schemas.jobs import JobResponse, JobStatus
from app.schemas.sky_patch import ErrorResponse

router = APIRouter(tags=["jobs"])

JOB_NOT_FOUND = "Job not found (unknown id, or expired)."


@router.get(
    "/jobs/{job_id}",
    name="get_job",
    response_model=JobResponse,
    summary="Poll a live (out-of-cache) query",
    responses={status.HTTP_404_NOT_FOUND: {"model": ErrorResponse}},
)
def get_job(job_id: UUID, jobs: JobsDep, response: Response) -> JobResponse:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=JOB_NOT_FOUND)
    if job.status in (JobStatus.QUEUED, JobStatus.RUNNING):
        response.headers["Retry-After"] = str(POLL_AFTER_S)
    return job
