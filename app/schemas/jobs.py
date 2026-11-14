"""Async job contract: 202 Accepted bodies and GET /api/v1/jobs/{job_id}."""

from datetime import datetime
from enum import StrEnum
from typing import Literal
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
    # ---- added fields (optional) ----
    stage: str | None = Field(
        default=None, description="queued | query | download | stack | render | done | failed"
    )
    progress: float | None = Field(default=None, ge=0, le=1, description="0..1")
    message: str | None = Field(default=None, description="Human-readable progress, in Spanish")
    eta_s: int | None = Field(default=None, ge=0, description="Estimated seconds left, when known")
    retry_after_ms: int | None = Field(default=None, ge=0, description="Suggested polling interval")


class JobCreate(_Strict):
    """Body of POST /api/v1/jobs. Give `target`, or both `ra` and `dec`."""

    kind: JobKind = JobKind.SKY_PATCH
    target: str | None = Field(default=None, examples=["WISE_0855"])
    ra: str | float | None = Field(
        default=None, description="Degrees or sexagesimal hours", examples=["08h55m10.831s", 133.7951]
    )
    dec: str | float | None = Field(
        default=None, description="Degrees or sexagesimal", examples=["-7d14m42.53s", -7.2451]
    )
    fov: float = Field(default=5.0, ge=1, le=30, description="Field of view (arcmin)")
    band: Literal["H2O", "CO2", "CO"] | None = Field(default=None, description="Echoed back only")
