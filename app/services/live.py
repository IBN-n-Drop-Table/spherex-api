"""Live processing for positions outside the Golden Path cache.

Runs scripts/generate_golden_path.py in-process (same code as the offline
cache, so the output has the same contract), with a smaller exposure budget.
The science stack (astropy, reproject, OpenCV...) is imported lazily: the API
itself never needs it, and without it live jobs fail with a clear message.
"""

import importlib.util
import logging
import re
import shutil
import sys
import threading
from functools import lru_cache
from types import ModuleType
from typing import Protocol
from uuid import UUID

from app.core.config import settings
from app.core.targets import Target
from app.schemas.sky_patch import SkyPatchResponse
from app.schemas.spectrum import SpectrumResponse

logger = logging.getLogger(__name__)

SCIENCE_MODULES = ("numpy", "astropy", "astroquery", "reproject", "cv2", "matplotlib", "PIL")


class LiveProcessingUnavailable(RuntimeError):
    pass


def live_processing_available() -> bool:
    return settings.pipeline_script.is_file() and all(
        importlib.util.find_spec(m) is not None for m in SCIENCE_MODULES
    )


def coordinate_target(ra_deg: float, dec_deg: float) -> Target:
    """A catalogue-less target at fixed coordinates (no proper motion)."""
    return Target(
        key="LIVE",
        name=f"RA {ra_deg:.4f} Dec {dec_deg:+.4f}",
        ra_deg=ra_deg,
        dec_deg=dec_deg,
        ref_ra_deg=ra_deg,
        ref_dec_deg=dec_deg,
        ref_epoch_yr=2016.0,
    )


class LiveRunner(Protocol):
    def sky_patch(self, target: Target, fov_arcmin: float, base_url: str, job_id: UUID) -> SkyPatchResponse: ...

    def spectrum(self, target: Target, fov_arcmin: float, base_url: str, job_id: UUID) -> SpectrumResponse: ...


_import_lock = threading.Lock()


@lru_cache
def _load_pipeline() -> ModuleType:
    with _import_lock:
        if not live_processing_available():
            raise LiveProcessingUnavailable(
                "Live processing is not installed on this server "
                "(pip install -r requirements-science.txt). Cached Golden Path targets still work."
            )
        name = "spherex_golden_path_pipeline"
        spec = importlib.util.spec_from_file_location(name, settings.pipeline_script)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module  # dataclasses resolve annotations through sys.modules
        spec.loader.exec_module(module)
        module.quiet_astropy()
        return module


class UpstreamUnavailable(RuntimeError):
    pass


# Raised by pyvo / urllib / http.client when IRSA is down or throttling.
_UPSTREAM_ERRORS = {"DALServiceError", "HTTPError", "URLError", "IncompleteRead",
                    "TimeoutError", "ConnectionError", "ConnectionResetError", "RemoteDisconnected"}


class PipelineLiveRunner:
    def _payload(self, target: Target, fov_arcmin: float, base_url: str, job_id: UUID) -> dict:
        pipeline = _load_pipeline()
        args = pipeline.default_args(
            fov=fov_arcmin,
            max_exposures_per_epoch=settings.live_max_exposures_per_epoch,
            workers=settings.live_download_workers,
        )
        uploader = pipeline.LocalStorage(base_url, url_path=f"live/{job_id}")
        try:
            return pipeline.build_payload(target, uploader, args, cached=False)
        except Exception as exc:
            shutil.rmtree(uploader.directory, ignore_errors=True)
            if type(exc).__name__ in _UPSTREAM_ERRORS:
                code = re.search(r"\b(5\d\d)\b", str(exc))
                detail = f"HTTP {code.group(1)}" if code else type(exc).__name__
                raise UpstreamUnavailable(
                    f"NASA/IRSA is unavailable or busy right now ({detail}). Try again in a few minutes."
                ) from exc
            raise

    def sky_patch(self, target: Target, fov_arcmin: float, base_url: str, job_id: UUID) -> SkyPatchResponse:
        return SkyPatchResponse.model_validate(self._payload(target, fov_arcmin, base_url, job_id))

    def spectrum(self, target: Target, fov_arcmin: float, base_url: str, job_id: UUID) -> SpectrumResponse:
        patch = self.sky_patch(target, fov_arcmin, base_url, job_id)
        return SpectrumResponse(
            source="target",
            target_name=patch.target_info.name,
            ra_deg=patch.patch.wcs.crval1,
            dec_deg=patch.patch.wcs.crval2,
            epoch_id=patch.timelapse_epochs[0].epoch_id,
            is_cached_golden_path=False,
            spectrum_102_bands=patch.spectrum_102_bands,
        )


def get_live_runner() -> LiveRunner:
    """FastAPI dependency (override it in tests)."""
    return PipelineLiveRunner()
