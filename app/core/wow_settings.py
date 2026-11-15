"""Settings for the AI chat and the "find what moves" game.

Kept apart from app/core/config.py so these features don't touch shared files.
Everything can be overridden with environment variables. API keys are read here
and only passed to the provider client: never logged, never returned.

Model ids checked on 2026-11-14 against the providers' docs:
* Groq (https://console.groq.com/docs/models): `openai/gpt-oss-120b` is a production
  model. `llama-3.3-70b-versatile` was shut down for free/developer tiers on 2026-08-16.
* DeepSeek (https://api-docs.deepseek.com/quick_start/pricing): `deepseek-flash` is the
  fast model (V4.1 Flash). `deepseek-chat` / `deepseek-reasoner` were discontinued.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def _str(name: str, default: str | None = None) -> str | None:
    return os.getenv(name) or default


@dataclass(frozen=True)
class ChatSettings:
    groq_api_key: str | None = field(default_factory=lambda: _str("GROQ_API_KEY"), repr=False)
    groq_model: str = field(default_factory=lambda: _str("GROQ_MODEL", "openai/gpt-oss-120b"))
    groq_base_url: str = field(default_factory=lambda: _str("GROQ_BASE_URL", "https://api.groq.com/openai/v1"))
    deepseek_api_key: str | None = field(default_factory=lambda: _str("DEEPSEEK_API_KEY"), repr=False)
    deepseek_model: str = field(default_factory=lambda: _str("DEEPSEEK_MODEL", "deepseek-flash"))
    deepseek_base_url: str = field(default_factory=lambda: _str("DEEPSEEK_BASE_URL", "https://api.deepseek.com"))
    # Per provider, retry included.
    provider_timeout_s: float = field(default_factory=lambda: _float("CHAT_PROVIDER_TIMEOUT_S", 8.0))
    max_tokens: int = field(default_factory=lambda: _int("CHAT_MAX_TOKENS", 600))
    # Per client IP, sliding window of 60 s (all chat requests, cached ones too).
    rate_per_min: int = field(default_factory=lambda: _int("CHAT_RATE_PER_MIN", 8))
    # Global cap of calls to the AI providers per UTC day; past it, canned answers.
    daily_provider_cap: int = field(default_factory=lambda: _int("CHAT_DAILY_PROVIDER_CAP", 1000))
    cache_ttl_s: int = field(default_factory=lambda: _int("CHAT_CACHE_TTL_S", 3600))
    cache_max_entries: int = field(default_factory=lambda: _int("CHAT_CACHE_MAX_ENTRIES", 500))


@dataclass(frozen=True)
class GameSettings:
    session_ttl_s: int = field(default_factory=lambda: _int("GAME_SESSION_TTL_S", 900))
    max_sessions: int = field(default_factory=lambda: _int("GAME_MAX_SESSIONS", 2000))
    crop_px: int = field(default_factory=lambda: _int("GAME_CROP_PX", 300))
    # WISE vs SPHEREx round pool (app/data/game_pool.json); GAME_POOL=0 serves only the Golden Path.
    pool_enabled: bool = field(default_factory=lambda: os.getenv("GAME_POOL", "1") != "0")
    # Per client IP, per minute.
    sessions_per_min: int = field(default_factory=lambda: _int("GAME_SESSIONS_PER_MIN", 10))
    guesses_per_min: int = field(default_factory=lambda: _int("GAME_GUESSES_PER_MIN", 60))
    finish_per_min: int = field(default_factory=lambda: _int("GAME_FINISH_PER_MIN", 5))
    scores_table: str = "game_scores"
