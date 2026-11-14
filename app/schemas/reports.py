"""Contract for citizen-science discovery reports and the community feed.

Field limits mirror the CHECK constraints in
supabase/migrations/20261114000000_citizen_reports.sql. Keep the two in sync,
so a bad report gets a 422 from the API instead of a database error.
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID

from pydantic import Field, HttpUrl, StringConstraints, model_validator

from app.schemas.sky_patch import _Strict


class Classification(StrEnum):
    ASTEROID = "asteroid"
    BROWN_DWARF = "brown_dwarf"
    HIGH_PROPER_MOTION_STAR = "high_proper_motion_star"
    VARIABLE_STAR = "variable_star"
    TRANSIENT_ANOMALY = "transient_anomaly"
    OTHER = "other"


Handle = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
Notes = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)]
TargetName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
AnomalyId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}$")]


class CitizenReportCreate(_Strict):
    """Body of POST /reports."""

    discoverer: Handle = Field(description="Display name or handle", examples=["stargazer_ana"])
    notes: Notes | None = Field(default=None, examples=["Moves left between epochs"])
    ra_deg: float = Field(ge=0, lt=360, description="Right ascension (deg, ICRS)")
    dec_deg: float = Field(ge=-90, le=90, description="Declination (deg, ICRS)")
    target_name: TargetName | None = Field(
        default=None,
        description="Golden Path target. Spellings like 'wise_0855' are stored as 'WISE 0855'.",
        examples=["WISE 0855"],
    )
    pixel_x: int | None = Field(default=None, ge=0, description="Column in the PNG (origin top-left)")
    pixel_y: int | None = Field(default=None, ge=0, description="Row in the PNG (origin top-left)")
    classification: Classification
    anomaly_id: AnomalyId | None = Field(
        default=None, description="Automated detection this report confirms", examples=["anom_09"]
    )
    snapshot_url: HttpUrl | None = Field(
        default=None, description="Screenshot of the discovery (http/https, max 2083 chars)"
    )

    @model_validator(mode="after")
    def _pixels_come_in_pairs(self) -> Self:
        if (self.pixel_x is None) != (self.pixel_y is None):
            raise ValueError("pixel_x and pixel_y must be given together")
        return self


class CitizenReport(CitizenReportCreate):
    """A stored report, as returned by every endpoint."""

    id: UUID
    created_at: datetime
    verifications_count: int = Field(ge=1, description="Includes the discoverer's own report")
    is_example: bool = Field(
        default=False,
        description="Added field. True for the demo examples the in-memory store loads with "
        "REPORTS_DEMO_SEED=1: not a real citizen report, label it as such",
    )
