"""Game "Encuentra lo que se mueve": rounds, ground truth, crops, sessions, scoring.

Only Barnard's Star and Epsilon Indi B, the two targets whose motion is
measurable in SPHEREx; never the anomaly detector (largely false positives).

Ground truth. For each visit the object's position comes from its Gaia DR3
reference position + proper motion (app/core/targets.py) propagated to the
visit date, projected with the patch WCS. Checked against the cached PNGs on
2026-11-14: the light centroid of the star is within 0.7 px (0.35″) of the
predicted position in all 7 visits (tests/test_game.py repeats the check).

Crops. Every patch is centred on the object at its first visit, so the answer
would always be "the centre". Each round therefore shows a `crop_px` window
cut from the 600 px images with Pillow, placed so the object lands at one of
25 positions (5 x 5 grid). The same window is cut from both visits (and from the
difference image for the hint). File names are salted hashes: they don't reveal
the window.

Score of a round (0-1000), all computed here:
    miss (distance to the object's path > HIT_RADIUS_PX): 0
    hit: 500 + 300 * (1 - d / HIT_RADIUS_PX) + 200 * max(0, 1 - t / 30 s)
where d is the distance in pixels from the guess to the segment joining the
object's two positions, and t the time the player took (`time_ms`, reported by
the client and clamped; worth at most 200 points).
"""

from __future__ import annotations

import logging
import math
import os
import random
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

from app.core.config import settings
from app.core.patch_geometry import SPHEREX_PIXEL_ARCSEC
from app.core.targets import CATALOG, Target
from app.core.urls import INTERNAL_BASE, to_relative
from app.core.wow_settings import GameSettings
from app.schemas.game import GameRound, GameSession, GuessResult, ImageSize, Point
from app.services.golden_path import cached_target

logger = logging.getLogger(__name__)

GAME_TARGETS = ("BARNARDS_STAR", "EPSILON_INDI_B")
HIT_RADIUS_PX = 20.0  # 10″ at 0.5″/px: about the radius of the star's blob in the PNG
SPEED_WINDOW_MS = 30_000
GRID_FRACTIONS = (0.2, 0.35, 0.5, 0.65, 0.8)
J2000 = datetime(2000, 1, 1, 12, tzinfo=UTC)

SCORING_ES = (  # user-facing: English
    "Miss (more than 20 pixels from the object's path): 0 points. Hit: 500 points, plus up to 300 "
    "for precision and up to 200 for speed (if you answer in under 30 s). Up to 1000 per round."
)


# --------------------------------------------------------------------------- #
# Geometry and ground truth
# --------------------------------------------------------------------------- #


def julian_year(when: datetime) -> float:
    return 2000.0 + (when - J2000).total_seconds() / 86400.0 / 365.25


def position_at(target: Target, when: datetime) -> tuple[float, float]:
    """Linear proper-motion propagation from the reference epoch (deg, ICRS)."""
    dt = julian_year(when) - target.ref_epoch_yr
    dec = target.ref_dec_deg + target.pm_dec_mas_yr / 3.6e6 * dt
    ra = target.ref_ra_deg + target.pm_ra_cosdec_mas_yr / 3.6e6 * dt / math.cos(math.radians(target.ref_dec_deg))
    return ra % 360.0, dec


def world_to_png(wcs: dict, height_px: int, ra: float, dec: float) -> tuple[float, float]:
    """(ra, dec) -> continuous PNG position (pixel i spans [i, i+1), +y down).
    Inverse of app.core.patch_geometry.png_to_world (+0.5: that one uses pixel centres)."""
    a0, d0 = math.radians(wcs["crval1"]), math.radians(wcs["crval2"])
    a, d = math.radians(ra), math.radians(dec)
    cosc = math.sin(d0) * math.sin(d) + math.cos(d0) * math.cos(d) * math.cos(a - a0)
    xi = math.degrees(math.cos(d) * math.sin(a - a0) / cosc)
    eta = math.degrees((math.cos(d0) * math.sin(d) - math.sin(d0) * math.cos(d) * math.cos(a - a0)) / cosc)
    det = wcs["cd1_1"] * wcs["cd2_2"] - wcs["cd1_2"] * wcs["cd2_1"]
    dx = (wcs["cd2_2"] * xi - wcs["cd1_2"] * eta) / det
    dy = (wcs["cd1_1"] * eta - wcs["cd2_1"] * xi) / det
    x_fits, y_fits = wcs["crpix1"] + dx, wcs["crpix2"] + dy
    return x_fits - 0.5, height_px - y_fits + 0.5


def distance_to_segment(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    (px, py), (ax, ay), (bx, by) = p, a, b
    vx, vy = bx - ax, by - ay
    length2 = vx * vx + vy * vy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / length2))
    return math.hypot(px - (ax + t * vx), py - (ay + t * vy))


def round_score(distance_px: float, time_ms: int) -> int:
    if distance_px > HIT_RADIUS_PX:
        return 0
    precision = 1.0 - distance_px / HIT_RADIUS_PX
    speed = max(0.0, 1.0 - min(time_ms, SPEED_WINDOW_MS) / SPEED_WINDOW_MS)
    return round(500 + 300 * precision + 200 * speed)


def _number(value: float, decimals: int) -> str:
    return f"{value:.{decimals}f}"


# --------------------------------------------------------------------------- #
# Round catalogue (from the cached Golden Path, never modified)
# --------------------------------------------------------------------------- #


def _static_path(url: str) -> Path:
    rel = to_relative(str(url))  # "/static/golden_path/x.png"
    return settings.static_dir / rel.removeprefix("/static/")


@dataclass(frozen=True)
class PairSpec:
    pair_id: str
    target: Target
    kind: str
    epoch_a: str  # later
    epoch_b: str  # earlier
    date_a: datetime
    date_b: datetime
    image_a: Path
    image_b: Path
    diff: Path
    size: tuple[int, int]
    pos_a: tuple[float, float]  # full-patch continuous PNG position, later visit
    pos_b: tuple[float, float]
    delta_t_days: float
    expected_shift_arcsec: float
    weak_signal: bool
    pixel_arcsec: float

    @property
    def shift_px(self) -> float:
        return math.dist(self.pos_a, self.pos_b)

    @property
    def difficulty(self) -> str:
        if self.weak_signal and self.shift_px < 8:
            return "hard"
        return "easy" if self.shift_px >= 15 else "medium"


def build_pairs() -> list[PairSpec]:
    pairs = []
    for key in GAME_TARGETS:
        target = CATALOG[key]
        patch = cached_target(target)
        if patch is None:
            logger.warning("Game: no cached data for %s, its rounds are skipped.", key)
            continue
        wcs = patch.patch.wcs.model_dump()
        h, w = patch.patch.height_px, patch.patch.width_px
        epochs = {e.epoch_id: e for e in patch.timelapse_epochs}
        diff = patch.difference_imaging
        for d in [diff, *diff.pairwise]:
            ea, eb = epochs[d.epoch_a], epochs[d.epoch_b]
            days = d.delta_t_days or (ea.date - eb.date).total_seconds() / 86400.0
            pm = math.hypot(target.pm_ra_cosdec_mas_yr, target.pm_dec_mas_yr) / 1000.0  # ″/yr
            shift = d.expected_shift_arcsec if d.expected_shift_arcsec is not None else round(pm * days / 365.25, 2)
            weak = d.weak_signal if d.weak_signal is not None else shift < SPHEREX_PIXEL_ARCSEC
            pairs.append(PairSpec(
                pair_id=f"{key}:{d.epoch_b}:{d.epoch_a}",
                target=target,
                kind=d.kind or "first_vs_last",
                epoch_a=d.epoch_a, epoch_b=d.epoch_b, date_a=ea.date, date_b=eb.date,
                image_a=_static_path(ea.image_url), image_b=_static_path(eb.image_url),
                diff=_static_path(d.subtracted_image_url),
                size=(w, h),
                pos_a=world_to_png(wcs, h, *position_at(target, ea.date)),
                pos_b=world_to_png(wcs, h, *position_at(target, eb.date)),
                delta_t_days=round(days, 2), expected_shift_arcsec=shift, weak_signal=bool(weak),
                pixel_arcsec=abs(wcs["cd2_2"]) * 3600.0,
            ))
    return pairs


@lru_cache
def game_pairs() -> tuple[PairSpec, ...]:
    return tuple(build_pairs())


# --------------------------------------------------------------------------- #
# Crops
# --------------------------------------------------------------------------- #


class CropStore:
    """Writes crop PNGs under static/game/, named by a salted hash (no answer leak)."""

    def __init__(self, directory: Path, crop_px: int, salt: str | None = None,
                 static_root: Path | None = None) -> None:
        self.directory, self.crop_px = directory, crop_px
        self.static_root = static_root or settings.static_dir  # served at /static
        self.salt = salt or os.getenv("GAME_SALT") or secrets.token_hex(16)
        self._lock = threading.Lock()

    def window(self, pair: PairSpec, fx: float, fy: float) -> tuple[int, int]:
        """Top-left corner of the window that puts the path's middle at (fx, fy) of the crop."""
        c = self.crop_px
        w, h = pair.size
        mx, my = (pair.pos_a[0] + pair.pos_b[0]) / 2, (pair.pos_a[1] + pair.pos_b[1]) / 2
        ox = min(max(round(mx - fx * c), 0), w - c)
        oy = min(max(round(my - fy * c), 0), h - c)
        return ox, oy

    def crop(self, src: Path, origin: tuple[int, int], tag: str) -> str:
        """Relative URL of the crop of `src` at `origin` (made once, then reused)."""
        name = sha256(f"{self.salt}|{src.name}|{origin}|{self.crop_px}|{tag}".encode()).hexdigest()[:24]
        path = self.directory / f"{name}.png"
        if not path.is_file():
            from PIL import Image  # optional at import time: only the game needs it

            with self._lock:
                if not path.is_file():
                    self.directory.mkdir(parents=True, exist_ok=True)
                    ox, oy = origin
                    with Image.open(src) as img:
                        piece = img.crop((ox, oy, ox + self.crop_px, oy + self.crop_px))
                        tmp = path.with_suffix(f".{secrets.token_hex(4)}.tmp")
                        piece.save(tmp, format="PNG", optimize=True)
                    os.replace(tmp, path)
        return f"/static/{path.relative_to(self.static_root).as_posix()}"


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #


class SessionExpired(Exception):
    pass


class SessionNotFound(Exception):
    pass


class RoundNotFound(Exception):
    pass


class AlreadyAnswered(Exception):
    pass


class AlreadyFinished(Exception):
    pass


class NothingPlayed(Exception):
    pass


@dataclass
class RoundState:
    public: GameRound
    pair: PairSpec
    pos_a: tuple[float, float]  # in crop coordinates
    pos_b: tuple[float, float]
    result: GuessResult | None = None


@dataclass
class Session:
    session_id: UUID
    rounds: dict[str, RoundState]
    order: list[str]
    expires_at: float
    finished: bool = False
    created: float = field(default_factory=time.monotonic)

    @property
    def total(self) -> int:
        return sum(r.result.score for r in self.rounds.values() if r.result)

    @property
    def hits(self) -> int:
        return sum(1 for r in self.rounds.values() if r.result and r.result.hit)

    @property
    def played(self) -> int:
        return sum(1 for r in self.rounds.values() if r.result)


def explanation(pair: PairSpec, hit: bool, distance_px: float) -> str:
    name = pair.target.name
    shift = _number(pair.expected_shift_arcsec, 1)
    days = round(pair.delta_t_days)
    pixels = round(pair.shift_px)
    lead = "You found it! " if hit else f"Not this time: the object was {round(distance_px)} pixels from your mark. "
    text = (f"{lead}{name} moved about {shift}″ in {days} days ({pixels} pixels in this image), "
            "according to its proper motion measured by Gaia DR3; its position in these SPHEREx "
            "images matches that prediction.")
    if pair.weak_signal:
        text += (f" That is less than one SPHEREx pixel ({_number(SPHEREX_PIXEL_ARCSEC, 2)}″), "
                 "which is why it barely shows in the blink.")
    return text


class GameService:
    def __init__(self, pairs: tuple[PairSpec, ...], cfg: GameSettings, crops: CropStore,
                 clock=time.monotonic, rng: random.Random | None = None) -> None:
        self.pairs, self.cfg, self.crops = pairs, cfg, crops
        self._clock = clock
        self._rng = rng or random.SystemRandom()
        self._sessions: dict[UUID, Session] = {}
        self._lock = threading.Lock()

    # -- creation ------------------------------------------------------------

    def _pick(self, n: int) -> list[PairSpec]:
        by = {d: [p for p in self.pairs if p.difficulty == d] for d in ("easy", "medium", "hard")}
        for group in by.values():
            self._rng.shuffle(group)
        chosen = by["easy"][:1] + by["medium"][: max(n - 2, 1)] + by["hard"][:1]
        rest = [p for p in by["easy"][1:] + by["medium"] + by["hard"] if p not in chosen]
        while len(chosen) < n and rest:  # not enough of some difficulty: fill
            chosen.append(rest.pop(0))
        order = {"easy": 0, "medium": 1, "hard": 2}
        return sorted(chosen[:n], key=lambda p: order[p.difficulty])

    def _make_round(self, pair: PairSpec) -> RoundState:
        fx, fy = self._rng.choice(GRID_FRACTIONS), self._rng.choice(GRID_FRACTIONS)
        ox, oy = self.crops.window(pair, fx, fy)
        tag = pair.pair_id
        url_a = self.crops.crop(pair.image_a, (ox, oy), tag)
        url_b = self.crops.crop(pair.image_b, (ox, oy), tag)
        hint = self.crops.crop(pair.diff, (ox, oy), tag) if pair.weak_signal else None
        public = GameRound(
            round_id=uuid4().hex[:12],
            target_id=pair.target.key, target_name=pair.target.name, kind=pair.kind,
            epoch_a=pair.epoch_a, epoch_b=pair.epoch_b, date_a=pair.date_a, date_b=pair.date_b,
            image_a_url=INTERNAL_BASE + url_a, image_b_url=INTERNAL_BASE + url_b,
            hint_image_url=INTERNAL_BASE + hint if hint else None,
            image_size=ImageSize(width=self.crops.crop_px, height=self.crops.crop_px),
            weak_signal=pair.weak_signal, difficulty=pair.difficulty,
            delta_t_days=pair.delta_t_days, expected_shift_arcsec=pair.expected_shift_arcsec,
        )
        return RoundState(public=public, pair=pair,
                          pos_a=(pair.pos_a[0] - ox, pair.pos_a[1] - oy),
                          pos_b=(pair.pos_b[0] - ox, pair.pos_b[1] - oy))

    def create(self, n_rounds: int) -> GameSession:
        if not self.pairs:
            raise RuntimeError("no game data")
        rounds = [self._make_round(p) for p in self._pick(n_rounds)]
        now = self._clock()
        session = Session(session_id=uuid4(), rounds={r.public.round_id: r for r in rounds},
                          order=[r.public.round_id for r in rounds], expires_at=now + self.cfg.session_ttl_s)
        with self._lock:
            self._prune(now)
            if len(self._sessions) >= self.cfg.max_sessions:
                raise OverflowError("too many active sessions")
            self._sessions[session.session_id] = session
        return self.public(session)

    def public(self, session: Session) -> GameSession:
        expires = datetime.now(UTC) + timedelta(seconds=max(session.expires_at - self._clock(), 0))
        return GameSession(session_id=session.session_id, expires_at=expires,
                           rounds=[session.rounds[r].public for r in session.order], scoring=SCORING_ES)

    def _prune(self, now: float) -> None:
        for sid in [s for s, v in self._sessions.items() if v.expires_at < now]:
            del self._sessions[sid]

    def _get(self, session_id: UUID) -> Session:
        session = self._sessions.get(session_id)
        if session is None:
            raise SessionNotFound()
        if session.expires_at < self._clock():
            del self._sessions[session_id]
            raise SessionExpired()
        return session

    # -- play ----------------------------------------------------------------

    def guess(self, session_id: UUID, round_id: str, x: float, y: float, time_ms: int) -> GuessResult:
        with self._lock:
            session = self._get(session_id)
            if session.finished:
                raise AlreadyFinished()
            state = session.rounds.get(round_id)
            if state is None:
                raise RoundNotFound()
            if state.result is not None:
                raise AlreadyAnswered()
            size = state.public.image_size
            if not (0 <= x < size.width and 0 <= y < size.height):
                raise ValueError(f"x and y must be inside the {size.width}x{size.height} image")
            distance = distance_to_segment((x, y), state.pos_b, state.pos_a)
            hit = distance <= HIT_RADIUS_PX
            mid = ((state.pos_a[0] + state.pos_b[0]) / 2, (state.pos_a[1] + state.pos_b[1]) / 2)
            state.result = GuessResult(
                hit=hit, distance_px=round(distance, 1), score=round_score(distance, time_ms),
                answer=Point(x=round(mid[0], 1), y=round(mid[1], 1)),
                answer_path=[Point(x=round(p[0], 1), y=round(p[1], 1)) for p in (state.pos_b, state.pos_a)],
                hit_radius_px=HIT_RADIUS_PX,
                explanation_es=explanation(state.pair, hit, distance),
            )
            session.expires_at = self._clock() + self.cfg.session_ttl_s
            return state.result

    def finish(self, session_id: UUID) -> Session:
        """Mark the session finished; the score is the sum of its stored round scores."""
        with self._lock:
            session = self._get(session_id)
            if session.finished:
                raise AlreadyFinished()
            if session.played == 0:
                raise NothingPlayed()
            session.finished = True
            return session


@lru_cache
def get_game_service() -> GameService:
    """FastAPI dependency (override it in tests via app.dependency_overrides)."""
    cfg = GameSettings()
    return GameService(game_pairs(), cfg, CropStore(settings.static_dir / "game", cfg.crop_px))
