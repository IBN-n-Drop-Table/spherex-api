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
                return max(hits[0] + self.window_s - now, 0.0)
            hits.append(now)
            return None

    def _prune(self, now: float) -> None:
        for key in [k for k, h in self._hits.items() if not h or h[-1] <= now - self.window_s]:
            del self._hits[key]


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def enforce(limiter: SlidingWindowLimiter, request: Request, what: str) -> None:
    """Raise 429 with Retry-After when this client is over the limit."""
    wait = limiter.check(client_ip(request))
    if wait is not None:
        retry = max(1, math.ceil(wait))
        raise HTTPException(
            status_code=429,
            detail=f"Demasiadas peticiones de {what}. Espera {retry} s y vuelve a intentarlo.",
            headers={"Retry-After": str(retry)},
        )
