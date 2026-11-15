"""POST /api/v1/chat and GET /api/v1/chat/suggestions (see docs/wow-contract.md)."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.core.wow_settings import ChatSettings
from app.schemas.chat import ChatRequest, ChatResponse, ChatSuggestions
from app.services.chat import ChatService, get_chat_service, suggestions_for
from app.services.rate_limit import SlidingWindowLimiter, enforce

router = APIRouter(tags=["chat"])


@lru_cache
def get_chat_limiter() -> SlidingWindowLimiter:
    return SlidingWindowLimiter(ChatSettings().rate_per_min, 60.0)


@router.post(
    "/chat",
    response_model=ChatResponse,
    response_model_exclude_none=True,  # `sources` is optional: absent unless SIMBAD facts were used
    summary="AI assistant that explains what the user sees (answers in English)",
    description="Groq first, DeepSeek as backup (8 s each). If both fail, there are no keys or "
    "the daily cap is reached, a canned answer comes back with `fallback: true`: this endpoint "
    "never returns 5xx because of a provider. 429 with Retry-After when a client asks too often.",
)
async def chat(
    body: ChatRequest,
    request: Request,
    service: Annotated[ChatService, Depends(get_chat_service)],
    limiter: Annotated[SlidingWindowLimiter, Depends(get_chat_limiter)],
) -> ChatResponse:
    enforce(limiter, request, "chat")
    return await service.answer(body)


@router.get("/chat/suggestions", response_model=ChatSuggestions,
            summary="4 suggested questions (English) for a target, or generic ones")
def chat_suggestions(
    target: Annotated[str | None, Query(description="Target key or name, e.g. BARNARDS_STAR")] = None,
) -> ChatSuggestions:
    key, items = suggestions_for(target)
    return ChatSuggestions(target=key, suggestions=items)
