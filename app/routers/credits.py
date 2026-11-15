from fastapi import APIRouter

from app.core.credits import (
    ACKNOWLEDGEMENT_SOURCE,
    CREDITS_SERVED,
    SIMBAD_ACKNOWLEDGEMENT,
    SOFTWARE,
    SPHEREX_ACKNOWLEDGEMENT,
)
from app.schemas.credits import CreditsResponse

router = APIRouter(tags=["credits"])


def credits() -> CreditsResponse:
    return CreditsResponse(data_credits=CREDITS_SERVED, acknowledgement=SPHEREX_ACKNOWLEDGEMENT,
                           acknowledgement_source=ACKNOWLEDGEMENT_SOURCE,
                           simbad_acknowledgement=SIMBAD_ACKNOWLEDGEMENT, software=SOFTWARE)


router.add_api_route(
    "/credits", credits, methods=["GET"], response_model=CreditsResponse,
    summary="Data used: SPHEREx (DR1, QR3), Gaia DR3, WISE and SIMBAD citations, and the SPHEREx and SIMBAD acknowledgements",
)
