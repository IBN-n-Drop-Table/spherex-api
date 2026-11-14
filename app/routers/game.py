"""Game "Encuentra lo que se mueve": /api/v1/game/* (see docs/wow-contract.md)."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from app.core.urls import to_public
from app.core.wow_settings import GameSettings
from app.routers.common import public_base
from app.schemas.game import (
    FinishRequest,
    FinishResult,
    GameSession,
    GuessRequest,
    GuessResult,
    Leaderboard,
    NewSession,
)
from app.services.game import (
    AlreadyAnswered,
    AlreadyFinished,
    GameService,
    NothingPlayed,
    RoundNotFound,
    SessionExpired,
    SessionNotFound,
    get_game_service,
)
from app.services.game_scores import ScoreService, get_score_service
from app.services.rate_limit import SlidingWindowLimiter, enforce

router = APIRouter(prefix="/game", tags=["game"])


@lru_cache
def get_game_limiters() -> dict[str, SlidingWindowLimiter]:
    cfg = GameSettings()
    return {
        "sessions": SlidingWindowLimiter(cfg.sessions_per_min, 60.0),
        "guess": SlidingWindowLimiter(cfg.guesses_per_min, 60.0),
        "finish": SlidingWindowLimiter(cfg.finish_per_min, 60.0),
    }


GameDep = Annotated[GameService, Depends(get_game_service)]
LimitersDep = Annotated[dict[str, SlidingWindowLimiter], Depends(get_game_limiters)]


def _session_errors(exc: Exception) -> HTTPException:
    if isinstance(exc, SessionNotFound):
        return HTTPException(404, "Game not found.")
    if isinstance(exc, SessionExpired):
        return HTTPException(410, "This game has expired. Start a new one.")
    if isinstance(exc, RoundNotFound):
        return HTTPException(404, "Round not found in this game.")
    if isinstance(exc, AlreadyAnswered):
        return HTTPException(409, "You already answered this round: there is one attempt per round.")
    if isinstance(exc, AlreadyFinished):
        return HTTPException(409, "This game is already finished.")
    if isinstance(exc, NothingPlayed):
        return HTTPException(409, "Play at least one round before saving your score.")
    raise exc


@router.post("/sessions", status_code=201, response_model=GameSession,
             summary="Start a game: 3-5 blink rounds with Barnard's Star and Epsilon Indi B")
def create_session(
    request: Request, game: GameDep, limiters: LimitersDep,
    body: Annotated[NewSession | None, Body()] = None,
) -> dict[str, Any]:
    enforce(limiters["sessions"], request, "new game")
    try:
        session = game.create((body or NewSession()).rounds)
    except OverflowError as exc:
        raise HTTPException(503, "Too many games in progress. Try again in a minute.") from exc
    except RuntimeError as exc:
        raise HTTPException(503, "The game is not available: the cached data is missing.") from exc
    return to_public(session, public_base(request))


@router.post("/guess", response_model=GuessResult,
             summary="One guess per round: where does the object move?")
def guess(body: GuessRequest, request: Request, game: GameDep, limiters: LimitersDep) -> GuessResult:
    enforce(limiters["guess"], request, "guess")
    try:
        return game.guess(body.session_id, body.round_id, body.x, body.y, body.time_ms)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise _session_errors(exc) from exc


@router.post("/finish", response_model=FinishResult,
             summary="Save the score, computed on the server from the session's guesses")
def finish(body: FinishRequest, request: Request, game: GameDep, limiters: LimitersDep,
           scores: Annotated[ScoreService, Depends(get_score_service)]) -> FinishResult:
    enforce(limiters["finish"], request, "score")
    try:
        session = game.finish(body.session_id)
    except Exception as exc:
        raise _session_errors(exc) from exc
    entry, backend = scores.add(body.nickname, session.total, session.hits, session.played,
                                len(session.order))
    return FinishResult(session_id=session.session_id, nickname=entry.nickname, score=entry.score,
                        hits=entry.hits, rounds_played=entry.rounds_played,
                        rounds_total=entry.rounds_total, stored_in=backend)


@router.get("/leaderboard", response_model=Leaderboard, summary="Top 20 scores")
def leaderboard(scores: Annotated[ScoreService, Depends(get_score_service)]) -> Leaderboard:
    return Leaderboard(entries=scores.top(20), backend=scores.backend)
