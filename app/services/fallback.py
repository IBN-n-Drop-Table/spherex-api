"""Offline-first fallback: when IRSA is slow or down, serve cached data instead.

Strategy doc, section 7B: if NASA's servers don't answer within 5 s (or
answer 429/5xx), the API serves cached data with a visible warning instead of
failing. The cached patch closest to the requested position is returned with
`fallback: true` and `fallback_message`. `target_info` keeps naming the cached
target, so the UI can say exactly what is being shown.
"""

from __future__ import annotations

import logging

from app.core.patch_geometry import angular_separation_arcsec
from app.schemas.sky_patch import FallbackReason, SkyPatchResponse
from app.schemas.spectrum import SpectrumResponse
from app.services import golden_path
from app.services.live import LiveProcessingUnavailable, UpstreamUnavailable

logger = logging.getLogger(__name__)

# One message per fallback_reason. Only an IRSA problem is "alta latencia".
FALLBACK_MESSAGE = "Showing cached data because the source servers are responding slowly"
LIVE_UNAVAILABLE_MESSAGE = (
    "These coordinates are not precomputed and live analysis is not available on this server right "
    "now. Pick one of the 5 challenge objects, or start a live analysis (POST /api/v1/jobs) later"
)
UNKNOWN_FALLBACK_MESSAGE = "Showing cached data: the live analysis could not be completed"


class NoCachedData(RuntimeError):
    pass


def nearest_cached_patch(ra_deg: float | None, dec_deg: float | None) -> tuple[SkyPatchResponse, float | None]:
    """(closest cached patch, distance in degrees). Without a position: the first cached one."""
    cached = golden_path.cached_patches()
    if not cached:
        raise NoCachedData("No cached Golden Path data on this server")
    if ra_deg is None or dec_deg is None:
        return cached[0], None
    best = min(cached, key=lambda p: angular_separation_arcsec(ra_deg, dec_deg, p.patch.wcs.crval1,
                                                                p.patch.wcs.crval2))
    distance = angular_separation_arcsec(ra_deg, dec_deg, best.patch.wcs.crval1, best.patch.wcs.crval2) / 3600
    return best, distance


def reason_code(reason: BaseException | str | None) -> FallbackReason:
    """Machine-readable cause of a fallback, for `fallback_reason`."""
    if isinstance(reason, UpstreamUnavailable):
        return "irsa_unavailable"
    if isinstance(reason, LiveProcessingUnavailable):
        return "live_processing_unavailable"
    return "unknown"


def message_for(patch: SkyPatchResponse, distance_deg: float | None,
                reason: BaseException | str | None = None) -> str:
    """English `fallback_message`, worded for its `fallback_reason`."""
    code = reason_code(reason)
    if code == "live_processing_unavailable":
        where = f", {distance_deg:.1f}° away" if distance_deg is not None else ""
        # The technical detail (no science stack) is for logs, not for users.
        return f"{LIVE_UNAVAILABLE_MESSAGE}. Showing the nearest precomputed object ({patch.target_info.name}{where})"
    msg = FALLBACK_MESSAGE if code == "irsa_unavailable" else UNKNOWN_FALLBACK_MESSAGE
    if distance_deg is not None and distance_deg > 0.25:
        msg += (f". There is no cached data for these coordinates: showing the nearest cached object "
                f"({patch.target_info.name}, {distance_deg:.1f}° away)")
    if reason:
        msg += f". Detail: {reason}"
    return msg


def sky_patch_fallback(ra_deg: float | None, dec_deg: float | None,
                       reason: BaseException | str | None = None) -> SkyPatchResponse:
    """`reason`: the exception that triggered the fallback (or its text)."""
    patch, distance = nearest_cached_patch(ra_deg, dec_deg)
    logger.warning("Serving fallback %s (%s)", patch.target_info.name, reason)
    return patch.model_copy(update={"fallback": True, "fallback_message": message_for(patch, distance, reason),
                                    "fallback_reason": reason_code(reason)})


def spectrum_fallback(ra_deg: float | None, dec_deg: float | None,
                      reason: BaseException | str | None = None) -> SpectrumResponse:
    return golden_path.target_spectrum(sky_patch_fallback(ra_deg, dec_deg, reason))
