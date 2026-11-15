"""Contract for the "find what moves" game: /api/v1/game/* (docs/wow-contract.md).

Pixel coordinates are CONTINUOUS positions in the round's images: origin at the
top-left corner, +x right, +y down, pixel i spans [i, i+1). The centre of the
first pixel is (0.5, 0.5). Both images of a round share the same size.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

NICKNAME_RE = re.compile(r"^[A-Za-z0-9ÁÉÍÓÚÜÑáéíóúüñ_-]{3,16}$")
MAX_TIME_MS = 600_000

Difficulty = Literal["easy", "medium", "hard"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ImageSize(_Strict):
    width: int
    height: int


class Region(_Strict):
    """Sky region shown in a round, for the viewer's shareable URL (?ra=..&dec=..&fov=..)."""
    ra_deg: float
    dec_deg: float
    fov_arcmin: float


class GameRound(_Strict):
    round_id: str
    target_id: str = Field(examples=["BARNARDS_STAR"])
    target_name: str = Field(examples=["Barnard's Star"])
    kind: Literal["consecutive", "first_vs_last"]
    epoch_a: str = Field(description="LATER visit (same convention as difference_imaging)")
    epoch_b: str = Field(description="EARLIER visit")
    date_a: datetime
    date_b: datetime
    image_a_url: HttpUrl = Field(description="Crop of the later visit")
    image_b_url: HttpUrl = Field(description="Crop of the earlier visit (same window)")
    hint_image_url: HttpUrl | None = Field(
        default=None,
        description="Crop of the difference image (later - earlier) for the same window. "
        "Only on weak_signal rounds, where the motion is hard to see in the blink.")
    image_size: ImageSize
    weak_signal: bool = Field(description="Expected shift below one native SPHEREx pixel (6.15″)")
    difficulty: Difficulty
    delta_t_days: float
    expected_shift_arcsec: float
    # Optional fields (added 2026-11-15). Golden Path rounds fill only `source`,
    # `telescope_*`, `pixel_scale_arcsec` and `hit_radius_px`.
    source: Literal["golden_path", "wise_vs_spherex"] | None = Field(
        default=None, description="golden_path: two SPHEREx visits; wise_vs_spherex: WISE (2010) vs SPHEREx")
    telescope_a: str | None = Field(default=None, description="Telescope of image_a (later)")
    telescope_b: str | None = Field(default=None, description="Telescope of image_b (earlier)")
    date_a_range: list[datetime] | None = Field(
        default=None, description="[start, end] of the data in image_a when it combines several dates")
    date_b_range: list[datetime] | None = Field(
        default=None, description="[start, end] of the data in image_b when it combines several dates")
    region: Region | None = Field(default=None, description="Centre and size of the round's images")
    pixel_scale_arcsec: float | None = Field(default=None, description="Arcsec per pixel of the round's PNGs")
    spherex_pixel_scale_arcsec: float | None = Field(
        default=None, description="Resolution of the SPHEREx data behind the image (12.9 for the DR1 HiPS)")
    hit_radius_px: float | None = Field(default=None, description="A guess within this distance is a hit")


class GameSession(_Strict):
    session_id: UUID
    expires_at: datetime = Field(description="Extended on every guess")
    rounds: list[GameRound]
    scoring: str = Field(description="How a round is scored, in English")
    fallback: bool | None = Field(
        default=None, description="true: the WISE vs SPHEREx pool was missing or failed and the rounds "
        "come from the Golden Path; false: pool rounds; null: the pool is not configured")


class NewSession(_Strict):
    rounds: int = Field(default=5, ge=3, le=5)


class Point(_Strict):
    x: float
    y: float


class GuessRequest(_Strict):
    session_id: UUID
    round_id: str = Field(min_length=1, max_length=64)
    x: float = Field(ge=0, description="Continuous pixel position in the round's images")
    y: float = Field(ge=0)
    time_ms: int = Field(ge=0, le=MAX_TIME_MS, description="Time the player took on this round")


class GuessResult(_Strict):
    hit: bool
    distance_px: float = Field(description="Distance from the guess to the object's path")
    score: int = Field(ge=0, le=1000)
    answer: Point = Field(description="Middle of the object's path between the two visits")
    answer_path: list[Point] = Field(
        description="Added field: the object's position in epoch_b then in epoch_a")
    hit_radius_px: float
    explanation_es: str


class FinishRequest(_Strict):
    session_id: UUID
    nickname: str

    @field_validator("nickname")
    @classmethod
    def _nickname(cls, v: str) -> str:
        v = v.strip()
        if not NICKNAME_RE.match(v):
            raise ValueError("nickname: 3-16 characters, letters, digits, '_' or '-'")
        return v


class ScoreEntry(_Strict):
    nickname: str
    score: int
    hits: int
    rounds_played: int
    rounds_total: int
    created_at: datetime


class FinishResult(_Strict):
    session_id: UUID
    nickname: str
    score: int = Field(description="Computed on the server from this session's guesses")
    hits: int
    rounds_played: int
    rounds_total: int
    stored_in: Literal["supabase", "memory"]


class Leaderboard(_Strict):
    entries: list[ScoreEntry]
    backend: Literal["supabase", "memory"]
