from fastapi import APIRouter

from app.core.credits import ACKNOWLEDGEMENT_SOURCE, DATA_CREDITS, SOFTWARE, SPHEREX_ACKNOWLEDGEMENT
from app.schemas.credits import CreditsResponse

router = APIRouter(tags=["credits"])


def credits() -> CreditsResponse:
    return CreditsResponse(data_credits=DATA_CREDITS, acknowledgement=SPHEREX_ACKNOWLEDGEMENT,
                           acknowledgement_source=ACKNOWLEDGEMENT_SOURCE, software=SOFTWARE)


router.add_api_route(
    "/credits", credits, methods=["GET"], response_model=CreditsResponse,
    summary="Data used: SPHEREx (DR1, QR3), Gaia DR3 and WISE citations, and the SPHEREx acknowledgement",
)
