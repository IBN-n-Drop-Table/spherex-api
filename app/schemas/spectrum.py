"""Response contract for GET /api/v1/spectrum."""

from typing import Literal

from pydantic import Field

from app.schemas.sky_patch import Spectrum, _Strict


class SpectrumResponse(_Strict):
    source: Literal["target", "anomaly"] = Field(
        description="`target`: photometry at the patch centre; `anomaly`: at a detected anomaly"
    )
    target_name: str | None = Field(examples=["WISE 0855"])
    anomaly_id: str | None = Field(default=None, examples=["anom_04"])
    ra_deg: float = Field(ge=0, lt=360, description="Where the spectrum was measured (deg, ICRS)")
    dec_deg: float = Field(ge=-90, le=90)
    epoch_id: str = Field(description="Epoch the photometry comes from", examples=["2025_M0"])
    is_cached_golden_path: bool
    spectrum_102_bands: Spectrum
