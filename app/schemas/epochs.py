"""Contracts for GET /api/v1/epochs and GET /api/v1/targets (new endpoints)."""

from datetime import datetime

from pydantic import Field

from app.schemas.sky_patch import FALLBACK_REASON_DOC, FallbackReason, _Strict


class EpochSummary(_Strict):
    epoch_id: str = Field(description="Same ids as sky-patch timelapse_epochs", examples=["2025_M0"])
    date: datetime = Field(description="Median observation time of the visit")
    date_start: str | None = Field(default=None, description="First exposure (YYYY-MM-DD)")
    date_end: str | None = Field(default=None, description="Last exposure (YYYY-MM-DD)")
    n_images: int | None = Field(default=None, ge=0, description="SPHEREx exposures in the visit")
    n_per_detector: dict[str, int] | None = None


class EpochsResponse(_Strict):
    ra_deg: float
    dec_deg: float
    n_images: int | None = Field(description="Total exposures over all visits, when known")
    epochs: list[EpochSummary]
    is_cached_golden_path: bool
    fallback: bool = False
    fallback_message: str | None = None
    fallback_reason: FallbackReason | None = Field(default=None, description=FALLBACK_REASON_DOC)


class TargetSummary(_Strict):
    key: str = Field(examples=["WISE_0855"])
    name: str = Field(examples=["WISE 0855"])
    ra_deg: float = Field(description="Catalogue position (deg, ICRS)")
    dec_deg: float
    fov_arcmin: float | None = Field(description="FOV of the cached patch")
    is_cached_golden_path: bool
    n_epochs: int | None = None
    sky_patch_query: str = Field(examples=["/api/v1/sky-patch?target=WISE_0855"])
