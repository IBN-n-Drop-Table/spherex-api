"""Response contract for GET /api/v1/sky-patch.

These models are the single source of truth for the JSON the frontend
consumes. They are also used to validate the cached Golden Path files at
startup, so a typo in a JSON fixture fails loudly instead of at demo time.
Pixel and WCS conventions are documented in app/core/patch_geometry.py.
"""

import math
from datetime import date, datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StringConstraints, model_validator


class _Strict(BaseModel):
    """Base model: reject unknown fields so the contract cannot drift silently."""

    model_config = ConfigDict(extra="forbid")


# SPHEREx L2 FLAGS names, decoded from the MSKNnnnn header keywords
# (e.g. TRANSIENT, HOT, NONFUNC, OUTLIER, SOURCE, GHOST, STREAK, BLOOM,
# SNOWBALL, PERSIST...). Kept open-ended because IRSA defines the list.
ContaminationFlag = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]*$")]

ImageFormat = Literal["png", "fits"]
ChemicalBand = Literal["H2O", "CO2", "CO"]
# Why cached data was served instead of a live answer (see app/services/fallback.py).
FallbackReason = Literal["irsa_unavailable", "live_processing_unavailable", "unknown"]
FALLBACK_REASON_DOC = ("Added field. Machine-readable cause when `fallback` is true: `irsa_unavailable` "
                       "(IRSA timed out, throttled or failed), `live_processing_unavailable` (this server "
                       "has no science stack). null when fallback is false")

DETECTION_LABEL = "Detección Asistida por Software (BETA)"
DETECTION_NOTICE = "Anomalía detectada por software: requiere verificación humana"
_IMAGE_FORMAT_DOC = "Format of the image URLs (`png` for rendered cutouts, `fits` for raw IRSA files)"


class TargetInfo(_Strict):
    name: str = Field(examples=["WISE 0855"])
    ra_deg: float = Field(ge=0, lt=360, description="Catalogue right ascension (deg, ICRS)")
    dec_deg: float = Field(ge=-90, le=90, description="Catalogue declination (deg, ICRS)")
    fov_arcmin: float = Field(gt=0, description="Field of view: patch edge length (arcmin)", examples=[5.0])
    is_cached_golden_path: bool


class PatchWCS(_Strict):
    """FITS WCS of the patch images: 1-based pixels, +Y up, North up, East left.

    `crval` is the patch centre, i.e. the target at the epoch-1 date.
    For high proper-motion stars this differs from `target_info`
    (WISE 0855 moved about 2 arcmin between 2010 and 2025)."""

    ctype1: Literal["RA---TAN"]
    ctype2: Literal["DEC--TAN"]
    crval1: float = Field(ge=0, lt=360)
    crval2: float = Field(ge=-90, le=90)
    crpix1: float
    crpix2: float
    cd1_1: float = Field(lt=0, description="deg/px; negative = East left")
    cd1_2: float = Field(ge=0, le=0, description="0 = North up (no rotation)")
    cd2_1: float = Field(ge=0, le=0, description="0 = North up (no rotation)")
    cd2_2: float = Field(gt=0, description="deg/px; positive = North up")


class Patch(_Strict):
    """Geometry shared by every image in the response (timelapse, filters, difference)."""

    width_px: int = Field(gt=0, examples=[600])
    height_px: int = Field(gt=0, examples=[600])
    wcs: PatchWCS

    @model_validator(mode="after")
    def _square_pixels(self) -> Self:
        if not math.isclose(-self.wcs.cd1_1, self.wcs.cd2_2, rel_tol=1e-6):
            raise ValueError("patch pixels must be square (|cd1_1| == cd2_2)")
        return self


class TimelapseEpoch(_Strict):
    epoch_id: str = Field(examples=["2025_M0"])
    date: datetime
    image_format: ImageFormat = Field(description=_IMAGE_FORMAT_DOC + "; also applies to chemical_filters")
    image_url: HttpUrl
    chemical_filters: dict[str, HttpUrl] = Field(
        description="Molecule name (H2O, CO2, CO) -> filtered image URL. A molecule is "
        "omitted when no exposure of that visit covers its wavelength window"
    )
    n_exposures: int | None = Field(
        default=None, ge=0, description="SPHEREx exposures stacked for this visit (added field)"
    )


class SpectralBand(_Strict):
    wavelength_um: float = Field(gt=0, description="Band centre (micron)")
    bandwidth_um: float = Field(gt=0, description="Band width (micron)")
    flux_mjy_sr: float = Field(description="Surface brightness (MJy/sr)")
    variance: float = Field(ge=0)
    detector: int = Field(ge=1, le=6, description="SPHEREx detector array (1-6)")
    quality: Literal["good", "low"] | None = Field(
        default=None, description="Added field. `low`: measured off-core because the core saturates"
    )


Spectrum = Annotated[list[SpectralBand], Field(max_length=102)]


class DetectedAnomaly(_Strict):
    anomaly_id: str
    ra_deg: float = Field(ge=0, lt=360)
    dec_deg: float = Field(ge=-90, le=90)
    pixel_x: int = Field(ge=0, description="0-based PNG column, origin top-left")
    pixel_y: int = Field(ge=0, description="0-based PNG row, origin top-left")
    snr_confidence: float = Field(ge=0, description="Signal-to-noise ratio")
    contamination_flags: list[ContaminationFlag]
    is_clean_detection: bool
    kind: Literal["appeared", "disappeared"] | None = Field(
        default=None,
        description="Added field. `appeared`: brighter in epoch_a (later); `disappeared`: brighter in epoch_b",
    )
    spectrum_epoch_id: str = Field(
        description="Epoch the spectrum was measured in: epoch_a if the source brightened, "
        "epoch_b if it faded"
    )
    spectrum_102_bands: Spectrum = Field(
        description="Spectrum at (ra_deg, dec_deg); channels without coverage are omitted"
    )


DetectorId = Annotated[int, Field(ge=1, le=6)]
_SHIFT_DOC = "Catalogue proper motion x delta_t (arcsec): how far the target should move"
_WEAK_DOC = ("True when that expected shift is below one native SPHEREx pixel (6.15\"): "
             "a dipole, if any, will be faint. Label it in the UI")


class PairwiseDifference(_Strict):
    """Added field. Difference between two consecutive visits (later - earlier).

    Images and metadata only: the anomaly detector does not run on pairs."""

    kind: Literal["consecutive"] = "consecutive"
    epoch_a: str = Field(description="Later visit (minuend): subtracted = epoch_a - epoch_b",
                         examples=["2025_M6"])
    epoch_b: str = Field(description="Earlier visit (subtrahend)", examples=["2025_M0"])
    image_format: ImageFormat = Field(default="png", description=_IMAGE_FORMAT_DOC)
    subtracted_image_url: HttpUrl
    delta_t_days: float = Field(gt=0, description="Days between the two visit medians")
    detectors_used: list[DetectorId] = Field(
        min_length=1, description="Detectors both visits share (the difference averages them)"
    )
    expected_shift_arcsec: float | None = Field(default=None, ge=0, description=_SHIFT_DOC)
    weak_signal: bool = Field(description=_WEAK_DOC)


class DifferenceImaging(_Strict):
    epoch_a: str = Field(description="Minuend epoch_id: subtracted = epoch_a - epoch_b", examples=["2025_M6"])
    epoch_b: str = Field(description="Subtrahend epoch_id", examples=["2025_M0"])
    image_format: ImageFormat = Field(description=_IMAGE_FORMAT_DOC)
    subtracted_image_url: HttpUrl
    detected_anomalies: list[DetectedAnomaly]
    detection_label: str | None = Field(
        default=DETECTION_LABEL, description="Added field. UI label for the detector"
    )
    detection_notice: str | None = Field(
        default=DETECTION_NOTICE, description="Added field. Every anomaly needs human verification"
    )
    # ---- added fields (optional): epoch-difference metadata ----
    kind: Literal["first_vs_last"] = Field(
        default="first_vs_last",
        description="Added field. This block is always the first vs the last visit "
        "(anomalies are detected on it); consecutive pairs are in `pairwise`",
    )
    delta_t_days: float | None = Field(default=None, gt=0, description="Added field. Days between the visits")
    detectors_used: list[DetectorId] | None = Field(
        default=None, description="Added field. Detectors both visits share"
    )
    expected_shift_arcsec: float | None = Field(default=None, ge=0, description="Added field. " + _SHIFT_DOC)
    weak_signal: bool | None = Field(default=None, description="Added field. " + _WEAK_DOC)
    pairwise: list[PairwiseDifference] = Field(
        default_factory=list,
        description="Added field. Consecutive-visit differences, in time order. Empty with fewer "
        "than 3 visits (the only pair is then this first-vs-last one)",
    )


class LightCurvePoint(_Strict):
    epoch_id: str
    date: date
    flux_mjy_sr: float | None = Field(
        description="Mean surface brightness (MJy/sr) over the channels shared by all epochs. "
        "`null` when there is no valid photometry (see `note`); never a fake 0"
    )
    variance: float | None = Field(ge=0)
    note: str | None = Field(default=None, description="Added field. Why flux is null, if it is")


class SkyPatchResponse(_Strict):
    target_info: TargetInfo
    patch: Patch
    timelapse_epochs: list[TimelapseEpoch]
    difference_imaging: DifferenceImaging
    spectrum_102_bands: Spectrum = Field(
        description="Target spectrum: up to 102 SPHEREx channels, from the visit in "
        "`spectrum_epoch_id` (epoch 1 when that field is absent)"
    )
    light_curve: list[LightCurvePoint]
    spectrum_epoch_id: str | None = Field(
        default=None, description="Added field. Visit the target spectrum was measured in"
    )
    # ---- added fields (optional; older clients can ignore them) ----
    saturated: bool | None = Field(
        default=None,
        description="True when the target core saturates SPHEREx (OVERFLOW/BLOOM): those pixels "
        "are shown in the PNGs but excluded from spectrum and light curve",
    )
    spectrum_message: str | None = Field(
        default=None, description="Why the spectrum is empty or partial, if it is"
    )
    band: ChemicalBand | None = Field(
        default=None, description="Echo of the `band` query parameter. All filters are always returned"
    )
    fallback: bool = Field(
        default=False, description="True when IRSA failed and this is cached data for another position"
    )
    fallback_message: str | None = None
    fallback_reason: FallbackReason | None = Field(default=None, description=FALLBACK_REASON_DOC)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        wcs, fov = self.patch.wcs, self.target_info.fov_arcmin
        for edge_px, scale, axis in ((self.patch.width_px, -wcs.cd1_1, "cd1_1"),
                                     (self.patch.height_px, wcs.cd2_2, "cd2_2")):
            if not math.isclose(scale * edge_px * 60.0, fov, rel_tol=1e-4):
                raise ValueError(f"{axis} * size must equal fov_arcmin / 60 ({fov} arcmin)")

        epochs = {e.epoch_id for e in self.timelapse_epochs}
        diff = self.difference_imaging
        if diff.epoch_a == diff.epoch_b or not {diff.epoch_a, diff.epoch_b} <= epochs:
            raise ValueError("epoch_a and epoch_b must be two different timelapse epoch_ids")

        for pair in diff.pairwise:
            if pair.epoch_a == pair.epoch_b or not {pair.epoch_a, pair.epoch_b} <= epochs:
                raise ValueError("pairwise epoch_a and epoch_b must be two different timelapse epoch_ids")

        if self.spectrum_epoch_id is not None and self.spectrum_epoch_id not in epochs:
            raise ValueError("spectrum_epoch_id must be a timelapse epoch_id")

        ids = [a.anomaly_id for a in diff.detected_anomalies]
        if len(ids) != len(set(ids)):
            raise ValueError("anomaly_id values must be unique")
        for a in diff.detected_anomalies:
            if a.pixel_x >= self.patch.width_px or a.pixel_y >= self.patch.height_px:
                raise ValueError(f"{a.anomaly_id}: pixel outside the {self.patch.width_px}px patch")
            if a.spectrum_epoch_id not in (diff.epoch_a, diff.epoch_b):
                raise ValueError(f"{a.anomaly_id}: spectrum_epoch_id must be epoch_a or epoch_b")
        return self


class ErrorFieldProblem(BaseModel):
    loc: list[str | int] = Field(description="Where the problem is, e.g. [\"query\", \"fov\"]")
    msg: str
    type: str


class ErrorResponse(BaseModel):
    """Every error the API returns has this shape (app/core/errors.py)."""

    detail: str = Field(description="Readable message, always a string")
    code: str = Field(
        description="Stable code: not_found, validation_error, service_unavailable, internal_error, "
        "or http_<status>",
        examples=["not_found"],
    )
    errors: list[ErrorFieldProblem] | None = Field(
        default=None, description="Field problems of a 422 validation error; null otherwise"
    )
