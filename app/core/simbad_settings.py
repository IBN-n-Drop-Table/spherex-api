"""Settings of the SIMBAD catalogue-objects feature (environment variables, all optional)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


@dataclass(frozen=True)
class SimbadSettings:
    # Synchronous TAP resource. The path /simbad/sim-tap/sync is listed on the TAP index page of the
    # Harvard mirror; the CDS host is the standard one. Point it at the mirror
    # (https://simbad.cfa.harvard.edu/simbad/sim-tap/sync) if CDS is down.
    tap_url: str = field(default_factory=lambda: os.getenv("SIMBAD_TAP_URL")
                         or "https://simbad.cds.unistra.fr/simbad/sim-tap/sync")
    # Connect + read budget towards SIMBAD (about 5 s in total); past it the fallback is served.
    timeout_s: float = field(default_factory=lambda: _float("SIMBAD_TIMEOUT_S", 5.0))
    # Per client IP, sliding window of 60 s (cached answers count too).
    rate_per_min: int = field(default_factory=lambda: _int("SIMBAD_RATE_PER_MIN", 20))
    # Global cap of queries to SIMBAD per second, all users: SIMBAD blocks IPs that go over about 5-6/s.
    global_qps: int = field(default_factory=lambda: _int("SIMBAD_GLOBAL_QPS", 3))
    cache_ttl_s: int = field(default_factory=lambda: _int("SIMBAD_CACHE_TTL_S", 3600))
    cache_max_entries: int = field(default_factory=lambda: _int("SIMBAD_CACHE_MAX_ENTRIES", 500))
    # Rows requested from SIMBAD per query (more than max_n: the circle is bigger than the square image).
    fetch_limit: int = field(default_factory=lambda: _int("SIMBAD_FETCH_LIMIT", 200))
