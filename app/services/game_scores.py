"""Game leaderboard storage: Supabase table `game_scores` (supabase/game_scores.sql),
or memory when SUPABASE_URL / SUPABASE_KEY are unset or the table doesn't answer.

Same policy as citizen reports: if Supabase fails mid-demo, log a warning and
stay on memory until restart, so the leaderboard never flips between stores.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime
from functools import lru_cache
from typing import Literal, Protocol

from app.core.config import settings
from app.core.wow_settings import GameSettings
from app.schemas.game import ScoreEntry

logger = logging.getLogger(__name__)

Backend = Literal["supabase", "memory"]
MEMORY_KEEP = 1000


class ScoreRepository(Protocol):
    def add(self, entry: ScoreEntry) -> ScoreEntry: ...

    def top(self, limit: int) -> list[ScoreEntry]: ...


def _rank(entries: list[ScoreEntry]) -> list[ScoreEntry]:
    return sorted(entries, key=lambda e: (-e.score, e.created_at))


class InMemoryScoreRepository:
    def __init__(self) -> None:
        self._entries: list[ScoreEntry] = []
        self._lock = threading.Lock()

    def add(self, entry: ScoreEntry) -> ScoreEntry:
        with self._lock:
            self._entries = _rank([*self._entries, entry])[:MEMORY_KEEP]
        return entry

    def top(self, limit: int) -> list[ScoreEntry]:
        with self._lock:
            return list(self._entries[:limit])


class SupabaseScoreRepository:
    COLUMNS = "nickname,score,hits,rounds_played,rounds_total,created_at"

    def __init__(self, url: str, key: str, table: str, timeout_s: float) -> None:
        from supabase import ClientOptions, create_client  # optional dependency

        self._client = create_client(url, key, options=ClientOptions(
            postgrest_client_timeout=timeout_s, postgrest_client_retry=False,
            auto_refresh_token=False, persist_session=False))
        self._table = table

    def ping(self) -> None:
        self._client.table(self._table).select("id").limit(1).execute()

    def add(self, entry: ScoreEntry) -> ScoreEntry:
        row = entry.model_dump(mode="json", exclude={"created_at"})
        res = self._client.table(self._table).insert(row).execute()
        return ScoreEntry.model_validate({k: res.data[0][k] for k in self.COLUMNS.split(",")})

    def top(self, limit: int) -> list[ScoreEntry]:
        res = (self._client.table(self._table).select(self.COLUMNS)
               .order("score", desc=True).order("created_at").limit(limit).execute())
        return [ScoreEntry.model_validate(r) for r in res.data]


class ScoreService:
    def __init__(self, primary: ScoreRepository | None, fallback: ScoreRepository) -> None:
        self._primary, self._fallback = primary, fallback
        self._lock = threading.Lock()

    @property
    def backend(self) -> Backend:
        return "supabase" if self._primary is not None else "memory"

    def _call(self, op: str, *args):
        primary = self._primary
        if primary is not None:
            try:
                return getattr(primary, op)(*args)
            except Exception as exc:
                with self._lock:
                    if self._primary is not None:
                        self._primary = None
                        logger.warning("Supabase game_scores failed (%s: %s): leaderboard in memory "
                                       "until restart.", op, type(exc).__name__)
        return getattr(self._fallback, op)(*args)

    def add(self, nickname: str, score: int, hits: int, played: int, total: int) -> tuple[ScoreEntry, Backend]:
        entry = ScoreEntry(nickname=nickname, score=score, hits=hits, rounds_played=played,
                           rounds_total=total, created_at=datetime.now(UTC))
        saved = self._call("add", entry)
        return saved, self.backend

    def top(self, limit: int = 20) -> list[ScoreEntry]:
        return self._call("top", limit)


def build_score_service() -> ScoreService:
    fallback = InMemoryScoreRepository()
    if not (settings.supabase_url and settings.supabase_key):
        logger.warning("SUPABASE_URL/SUPABASE_KEY not set: game leaderboard in memory.")
        return ScoreService(None, fallback)
    try:
        primary = SupabaseScoreRepository(settings.supabase_url, settings.supabase_key,
                                          GameSettings().scores_table, settings.supabase_timeout_s)
        primary.ping()
    except Exception as exc:
        logger.warning("Supabase game_scores not reachable (%s): leaderboard in memory. "
                       "Run supabase/game_scores.sql to create the table.", type(exc).__name__)
        return ScoreService(None, fallback)
    return ScoreService(primary, fallback)


@lru_cache
def get_score_service() -> ScoreService:
    return build_score_service()
