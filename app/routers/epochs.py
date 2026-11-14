from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.core.coords import CoordinateError, parse_dec, parse_ra
from app.core.target_stories import story_fields
from app.core.targets import CATALOG
from app.routers.common import RunnerDep
from app.schemas.epochs import EpochsResponse, EpochSummary, TargetSummary
from app.schemas.sky_patch import ErrorResponse, SkyPatchResponse
from app.services.fallback import message_for, nearest_cached_patch, reason_code
from app.services.golden_path import cached_target, find_sky_patch
from app.services.live import LiveProcessingUnavailable, UpstreamUnavailable

router = APIRouter(tags=["epochs"])


def _from_patch(patch: SkyPatchResponse) -> list[EpochSummary]:
    return [EpochSummary(epoch_id=e.epoch_id, date=e.date, n_images=e.n_exposures)
            for e in patch.timelapse_epochs]


def _total(epochs: list[EpochSummary]) -> int | None:
    counts = [e.n_images for e in epochs]
    return sum(counts) if all(c is not None for c in counts) else None


@router.get(
    "/epochs",
    response_model=EpochsResponse,
    summary="Visits (epochs) covering a position: metadata only, no image downloads",
    description="Cached Golden Path targets answer instantly. Other positions make one IRSA "
    "SIA query (at most SIA_BUDGET_S = 5 s); if IRSA is slow or down, the epochs of the "
    "nearest cached target are returned with `fallback: true`.",
    responses={status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorResponse}},
)
def get_epochs(
    runner: RunnerDep,
    ra: Annotated[str, Query(description="Degrees or sexagesimal hours", examples=["08h55m10.831s"])],
    dec: Annotated[str, Query(description="Degrees or sexagesimal", examples=["-7d14m42.53s"])],
) -> EpochsResponse:
    try:
        ra_deg, dec_deg = parse_ra(ra), parse_dec(dec)
    except CoordinateError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

    cached = find_sky_patch(ra=ra_deg, dec=dec_deg)
    if cached is not None:
        epochs = _from_patch(cached)
        return EpochsResponse(ra_deg=ra_deg, dec_deg=dec_deg, n_images=_total(epochs), epochs=epochs,
                              is_cached_golden_path=True)
    try:
        epochs = [EpochSummary(**e) for e in runner.epochs(ra_deg, dec_deg)]
        return EpochsResponse(ra_deg=ra_deg, dec_deg=dec_deg, n_images=_total(epochs), epochs=epochs,
                              is_cached_golden_path=False)
    except (UpstreamUnavailable, LiveProcessingUnavailable) as exc:
        patch, distance = nearest_cached_patch(ra_deg, dec_deg)
        epochs = _from_patch(patch)
        return EpochsResponse(ra_deg=ra_deg, dec_deg=dec_deg, n_images=_total(epochs), epochs=epochs,
                              is_cached_golden_path=True, fallback=True,
                              fallback_message=message_for(patch, distance, exc),
                              fallback_reason=reason_code(exc))


@router.get("/targets", response_model=list[TargetSummary],
            summary="The 5 challenge targets (demo buttons), with cache status")
def get_targets() -> list[TargetSummary]:
    out = []
    for t in CATALOG.values():
        patch = cached_target(t)
        out.append(TargetSummary(
            key=t.key, name=t.name, ra_deg=t.ra_deg, dec_deg=t.dec_deg,
            fov_arcmin=patch.target_info.fov_arcmin if patch else None,
            is_cached_golden_path=patch is not None,
            n_epochs=len(patch.timelapse_epochs) if patch else None,
            sky_patch_query=f"/api/v1/sky-patch?target={t.key}",
            **story_fields(t.key),
        ))
    return out
