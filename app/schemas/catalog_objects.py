"""Contract for GET /api/v1/catalog-objects (objects catalogued in SIMBAD, projected onto the image)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.credits import DataCredit

CatalogSource = Literal["simbad", "cache", "precomputed", "none"]
CatalogFallbackReason = Literal["simbad_unavailable", "simbad_rate_limited"]

NOTICE = ("Objects catalogued in SIMBAD, shown at their expected position on this image. "
          "They are not SPHEREx detections.")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CatalogObject(_Strict):
    id: str = Field(description="SIMBAD main identifier", examples=["* 699"])
    otype: str = Field(description="SIMBAD object type code", examples=["Flare*"])
    otype_label: str = Field(description="Readable type, as SIMBAD labels it (or the code)", examples=["Flare Star"])
    x: float = Field(description="Image x, continuous, 0 = left edge, +x right, pixel i spans [i, i+1)")
    y: float = Field(description="Image y, continuous, 0 = TOP edge, +y down (PNG convention, not FITS)")
    mag: float | None = Field(description="Magnitude in `mag_band`, null if SIMBAD has none")
    mag_band: str | None = Field(description="Photometric band of `mag` (V, G, R, J, H or K), null with `mag`")
    nbref: int = Field(description="Number of bibliographic references in SIMBAD (the relevance ranking)")
    simbad_url: str
    proper_motion_applied: bool = Field(
        description="True: the J2000 position was moved to `epoch_applied` with SIMBAD's proper motion. "
        "False: SIMBAD has none for this object, so the marker is at its J2000 position")


class EpochApplied(_Strict):
    epoch_id: str | None = Field(description="Visit the image belongs to (Golden Path); null for coordinates")
    date: str = Field(description="Date the positions were moved to, UTC ISO 8601")
    julian_year: float = Field(description="The same date as a Julian year", examples=[2025.65])


class ImageSize(_Strict):
    width: int
    height: int


class CatalogObjectsResponse(_Strict):
    objects: list[CatalogObject]
    image_size: ImageSize
    epoch_applied: EpochApplied
    truncated: bool = Field(description="True when more objects were in the image than `max_n` (or than SIMBAD returned)")
    source: CatalogSource = Field(
        description="`simbad` live, `cache` (this server's memory), `precomputed` (Golden Path file), "
        "`none` (nothing answered: `objects` is empty)")
    fallback_reason: CatalogFallbackReason | None = Field(
        description="Why SIMBAD was not used, null when it was (or a fresh cache entry answered)")
    data_credits: list[DataCredit]
    # ---- added fields ----
    notice: str = Field(description="English label to show next to the markers: they are catalogue entries, "
                        "not detections")
    fallback_message: str | None = Field(default=None, description="English text for the UI when `fallback_reason` is set")
