"""Live processing for positions outside the Golden Path cache.

Runs scripts/generate_golden_path.py in-process (same code as the offline
cache, so the output has the same contract), downloading only
LIVE_MAX_PER_DETECTOR cutouts per detector and visit. Measured 2026-11-14:
~40-100 s for Barnard (18 cutouts, 4 workers), peak RSS ~200 MB.
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
from collections.abc import Callable
from typing import Protocol
from uuid import UUID

from app.core.config import settings
from app.core.targets import Target
from app.schemas.sky_patch import SkyPatchResponse
from app.core.urls import to_internal
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


ProgressFn = Callable[[str, float, str], None]


class LiveRunner(Protocol):
    def sky_patch(self, target: Target, fov_arcmin: float, job_id: UUID,
                  progress: ProgressFn | None = None) -> SkyPatchResponse: ...

    def spectrum(self, target: Target, fov_arcmin: float, job_id: UUID,
                 progress: ProgressFn | None = None) -> SpectrumResponse: ...

    def epochs(self, ra_deg: float, dec_deg: float) -> list[dict]: ...


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


# Raised by requests / pyvo / urllib / http.client when IRSA is down or throttling.
_UPSTREAM_ERRORS = {"IrsaUnavailable", "DALServiceError", "HTTPError", "URLError", "IncompleteRead",
                    "TimeoutError", "ConnectionError", "ConnectionResetError", "RemoteDisconnected",
                    "ReadTimeout", "ConnectTimeout", "Timeout", "SSLError"}


def _upstream(exc: Exception) -> UpstreamUnavailable | None:
    if type(exc).__name__ not in _UPSTREAM_ERRORS:
        return None
    code = re.search(r"\b(429|5\d\d)\b", str(exc))
    detail = f"HTTP {code.group(1)}" if code else type(exc).__name__
    return UpstreamUnavailable(f"NASA/IRSA is unavailable or busy right now ({detail}). Try again in a few minutes.")


class PipelineLiveRunner:
    """Live mode: the same pipeline as the Golden Path, with the smallest download
    budget (settings.live_max_per_detector cutouts per detector and visit)."""

    def _args(self, fov_arcmin: float):
        return _load_pipeline().default_args(
            fov=min(fov_arcmin, settings.live_max_fov_arcmin),
            max_per_detector=settings.live_max_per_detector,
            workers=settings.live_download_workers,
            sia_budget_s=settings.sia_budget_s,
            retry_base_delay=2.0,
            download_timeout=60.0,
            seconds_per_cutout=settings.cutout_seconds_estimate,
            no_cache=True,  # ephemeral disk on free hosting: don't let the cutout cache grow
        )

    def _payload(self, target: Target, fov_arcmin: float, job_id: UUID, progress: ProgressFn | None) -> dict:
        pipeline = _load_pipeline()
        uploader = pipeline.LocalStorage(url_path=f"live/{job_id}")
        try:
            return pipeline.build_payload(target, uploader, self._args(fov_arcmin), cached=False,
                                          progress=progress)
        except Exception as exc:
            shutil.rmtree(uploader.directory, ignore_errors=True)
            mapped = _upstream(exc)
            if mapped is not None:
                raise mapped from exc
            raise

    def sky_patch(self, target: Target, fov_arcmin: float, job_id: UUID,
                  progress: ProgressFn | None = None) -> SkyPatchResponse:
        return SkyPatchResponse.model_validate(to_internal(self._payload(target, fov_arcmin, job_id, progress)))

    def spectrum(self, target: Target, fov_arcmin: float, job_id: UUID,
                 progress: ProgressFn | None = None) -> SpectrumResponse:
        from app.services.golden_path import target_spectrum

        return target_spectrum(self.sky_patch(target, fov_arcmin, job_id, progress), cached=False)

    def epochs(self, ra_deg: float, dec_deg: float) -> list[dict]:
        """Visit summary from one SIA query (no downloads). Same epoch_ids as sky-patch."""
        pipeline = _load_pipeline()
        from astropy.coordinates import SkyCoord

        coord = SkyCoord(ra_deg, dec_deg, unit="deg")
        try:
            rows = pipeline.dedupe(pipeline.query_sia(coord, pipeline.WIDE_COLLECTIONS, settings.sia_budget_s),
                                   pipeline.WIDE_COLLECTIONS)
        except Exception as exc:
            mapped = _upstream(exc)
            if mapped is not None:
                raise mapped from exc
            raise
        args = self._args(5.0)
        visits = pipeline.pick_epochs(pipeline.group_epochs(rows, args.epoch_gap_days), "all", args.min_exposures)
        return pipeline.summarize_visits(visits or [])


def get_live_runner() -> LiveRunner:
    """FastAPI dependency (override it in tests)."""
    return PipelineLiveRunner()
