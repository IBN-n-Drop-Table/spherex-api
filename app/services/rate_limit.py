"""Best-effort per-IP rate limiting, in memory.

Good enough for one process (Render runs a single instance with one worker);
counters reset on restart and are not shared between instances. The client IP
is `request.client.host`, which uvicorn already takes from X-Forwarded-For
(`--proxy-headers`), so a client that forges that header can dodge the limit:
the global daily cap of the chat is the real budget guard.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque

from fastapi import HTTPException, Request

MAX_KEYS = 20_000  # memory bound: past this, idle keys are dropped


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_s: float, clock=time.monotonic) -> None:
        self.limit = limit
        self.window_s = window_s
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> float | None:
        """Count one hit for `key`. None if allowed, else the seconds to wait."""
        now = self._clock()
        with self._lock:
            hits = self._hits.get(key)
            if hits is None:
                if len(self._hits) >= MAX_KEYS:
                    self._prune(now)
                hits = self._hits[key] = deque()
            while hits and hits[0] <= now - self.window_s:
                hits.popleft()
            if len(hits) >= self.limit:
                if not hits:  # limit 0: everything is blocked
                    return self.window_s
                return max(hits[0] + self.window_s - now, 0.0)
            hits.append(now)
            return None

    def _prune(self, now: float) -> None:
        for key in [k for k, h in self._hits.items() if not h or h[-1] <= now - self.window_s]:
            del self._hits[key]


class TokenBucket:
    """Token bucket: up to `burst` calls at once, then `rate_per_s` calls per second on average.

    Same `check(key)` contract as SlidingWindowLimiter (None if allowed, else the seconds to wait),
    but one shared bucket: the key is ignored. Used for the global pace of queries to SIMBAD.
    """

    def __init__(self, burst: int, rate_per_s: float, clock=time.monotonic) -> None:
        if burst < 0 or rate_per_s < 0:
            raise ValueError("burst and rate_per_s must be >= 0")
        self.burst = burst
        self.rate_per_s = rate_per_s
        self._clock = clock
        self._tokens = float(burst)
        self._last = clock()
        self._lock = threading.Lock()

    def _refill(self, now: float) -> None:
        self._tokens = min(float(self.burst), self._tokens + (now - self._last) * self.rate_per_s)
        self._last = now

    def check(self, key: str = "") -> float | None:
        with self._lock:
            now = self._clock()
            self._refill(now)
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return None
            if self.rate_per_s <= 0:
                return float("inf")
            return (1.0 - self._tokens) / self.rate_per_s

    @property
    def tokens(self) -> float:
        """Tokens available now (for tests and logs)."""
        with self._lock:
            self._refill(self._clock())
            return self._tokens


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def enforce(limiter: SlidingWindowLimiter, request: Request, what: str) -> None:
    """Raise 429 with Retry-After when this client is over the limit."""
    wait = limiter.check(client_ip(request))
    if wait is not None:
        retry = max(1, math.ceil(wait))
        raise HTTPException(
            status_code=429,
            detail=f"Too many {what} requests. Wait {retry} s and try again.",
            headers={"Retry-After": str(retry)},
        )
