"""GET /api/v1/loading-phrases: frases de las pantallas de carga.

Sin dependencias (solo app/core/loading_phrases.py) para que responda aunque
falle todo lo demás: el frontend lo usa mientras el servidor despierta.
"""

from fastapi import APIRouter, Response
from pydantic import BaseModel

from app.core.loading_phrases import LOADING_PHRASES

router = APIRouter(tags=["meta"])

CACHE_CONTROL = "public, max-age=3600"


class LoadingPhrases(BaseModel):
    phrases: dict[str, list[str]]


@router.get("/loading-phrases", response_model=LoadingPhrases,
            summary="Light-hearted phrases for loading screens, by category (English)")
def loading_phrases(response: Response) -> LoadingPhrases:
    response.headers["Cache-Control"] = CACHE_CONTROL
    return LoadingPhrases(phrases={k: list(v) for k, v in LOADING_PHRASES.items()})
