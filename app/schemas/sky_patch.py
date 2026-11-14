"""Response contract for GET /api/v1/sky-patch.

These models are the single source of truth for the JSON the frontend
consumes. They are also used to validate the cached Golden Path files at
startup, so a typo in a JSON fixture fails loudly instead of at demo time.
"""

from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StringConstraints


class _Strict(BaseModel):
    """Base model: reject unknown fields so the contract cannot drift silently."""

    model_config = ConfigDict(extra="forbid")


# SPHEREx L2 FLAGS names, decoded from the MSKNnnnn header keywords
# (e.g. TRANSIENT, HOT, NONFUNC, OUTLIER, SOURCE, GHOST, STREAK, BLOOM,
# SNOWBALL, PERSIST...). Kept open-ended because IRSA defines the list.
ContaminationFlag = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]*$")]


class TargetInfo(_Strict):
    name: str = Field(examples=["WISE 0855"])
    ra_deg: float = Field(ge=0, lt=360, description="Right ascension (deg, ICRS)")
    dec_deg: float = Field(ge=-90, le=90, description="Declination (deg, ICRS)")
    fov_arcmin: float = Field(gt=0, description="Field of view (arcmin)")
    is_cached_golden_path: bool


class TimelapseEpoch(_Strict):
    epoch_id: str = Field(examples=["2025_M0"])
    date: datetime
    image_url: HttpUrl
    chemical_filters: dict[str, HttpUrl] = Field(
        description="Molecule name (e.g. H2O, CO2) -> filtered image URL"
    )


class DetectedAnomaly(_Strict):
    anomaly_id: str
    ra_deg: float = Field(ge=0, lt=360)
    dec_deg: float = Field(ge=-90, le=90)
    pixel_x: int = Field(ge=0, description="Column in the PNG (origin top-left)")
    pixel_y: int = Field(ge=0, description="Row in the PNG (origin top-left)")
    snr_confidence: float = Field(ge=0, description="Signal-to-noise ratio")
    contamination_flags: list[ContaminationFlag]
    is_clean_detection: bool


class DifferenceImaging(_Strict):
    subtracted_image_url: HttpUrl
    detected_anomalies: list[DetectedAnomaly]


class SpectralBand(_Strict):
    wavelength_um: float = Field(gt=0, description="Band centre (micron)")
    bandwidth_um: float = Field(gt=0, description="Band width (micron)")
    flux_mjy_sr: float = Field(description="Surface brightness (MJy/sr)")
    variance: float = Field(ge=0)
    detector: int = Field(ge=1, le=6, description="SPHEREx detector array (1-6)")


class LightCurvePoint(_Strict):
    epoch_id: str
    date: date
    flux_mjy_sr: float
    variance: float = Field(ge=0)


class SkyPatchResponse(_Strict):
    target_info: TargetInfo
    timelapse_epochs: list[TimelapseEpoch]
    difference_imaging: DifferenceImaging
    spectrum_102_bands: list[SpectralBand] = Field(
        description="Up to 102 SPHEREx spectral channels (mock has a subset)"
    )
    light_curve: list[LightCurvePoint]


class ErrorResponse(BaseModel):
    detail: str
