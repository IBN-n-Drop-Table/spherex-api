"""Async job contract: 202 Accepted bodies and GET /api/v1/jobs/{job_id}."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import Field

from app.schemas.sky_patch import SkyPatchResponse, _Strict
from app.schemas.spectrum import SpectrumResponse


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class JobKind(StrEnum):
    SKY_PATCH = "sky_patch"
    SPECTRUM = "spectrum"


class JobResponse(_Strict):
    job_id: UUID
    kind: JobKind
    status: JobStatus
    result: SkyPatchResponse | SpectrumResponse | None = Field(
        default=None, description="Set when status is `completed`; same body as the 200 response"
    )
    error: str | None = Field(default=None, description="Set when status is `failed`")
    created_at: datetime
    updated_at: datetime
