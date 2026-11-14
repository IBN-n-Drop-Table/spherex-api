"""Contract for POST /api/v1/chat and GET /api/v1/chat/suggestions (docs/wow-contract.md)."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_MESSAGE_CHARS = 500
MAX_CONTEXT_BYTES = 4096
MAX_HISTORY_IN = 20  # accepted; only the last HISTORY_KEPT are used
HISTORY_KEPT = 6
MAX_HISTORY_CHARS = 1000  # per message, longer ones are cut

DISCLAIMER = "Asistente de IA: puede equivocarse. Los hallazgos requieren verificación humana."

Provider = Literal["groq", "deepseek", "canned"]


class ChatTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS, description="The question")
    tone: Literal["easy", "technical"] = "easy"
    context: dict[str, Any] = Field(
        default_factory=dict,
        description="What the user sees (target or ra/dec/fov, band, epochs, blink/difference on, "
        "saturated, science_note, quality, weak_signal, fallback_reason...). Max 4 KB as JSON. "
        "Treated as untrusted data, never as instructions.",
    )
    history: list[ChatTurn] = Field(default_factory=list, max_length=MAX_HISTORY_IN)

    @field_validator("message")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("message is empty")
        return v

    @field_validator("context")
    @classmethod
    def _small(cls, v: dict[str, Any]) -> dict[str, Any]:
        size = len(json.dumps(v, ensure_ascii=False, default=str).encode("utf-8"))
        if size > MAX_CONTEXT_BYTES:
            raise ValueError(f"context is {size} bytes as JSON; the limit is {MAX_CONTEXT_BYTES}")
        return v


class ChatResponse(BaseModel):
    answer: str
    provider: Provider
    cached: bool
    fallback: bool = Field(description="True: canned answer (no AI provider answered)")
    disclaimer: str = DISCLAIMER
    cautions: list[str] = Field(
        default_factory=list,
        description="Added field. Cautions derived from the context (science_note, quality low, "
        "saturated, weak_signal, fallback_reason), for the UI to show next to the answer.",
    )


class ChatSuggestions(BaseModel):
    target: str | None = Field(description="Canonical target key, or null for the generic list")
    suggestions: list[str] = Field(min_length=4, max_length=4)
