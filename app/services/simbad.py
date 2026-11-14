"""SIMBAD TAP client, TTL cache, global rate cap and precomputed fallback.

Offline-first, like the rest of the app: SIMBAD gets `timeout_s` seconds; if it fails, is over
the global rate cap or answers garbage, the last cached result for the same query (even a stale one)
is served, then the precomputed Golden Path result, then an empty list. The reason is always reported.

Rows are kept in J2000 as SIMBAD gives them: the epoch and the projection depend on the image,
not on the query, so they are applied per request (app/services/catalog_objects.py).

Columns used (all confirmed in the VOSI /tables listing of the SIMBAD Harvard mirror on 2026-11-15):
basic.{main_id, otype, ra, dec, pmra, pmdec, nbref, oid}, otypedef.{otype, label},
allfluxes.{oidref, V, G, R, J, H, K}. NOT verified against a live query (the service was unreachable):
the exact JSON layout of a TAP answer, the join semantics, and that `ORDER BY` + `TOP` behave as written.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Any

import httpx

from app.core.simbad_otypes import TypeFilter, adql_condition, matches
from app.core.simbad_settings import SimbadSettings
from app.services.rate_limit import SlidingWindowLimiter

logger = logging.getLogger(__name__)

MAG_BANDS = ("V", "G", "R", "J", "H", "K")  # first one with a value wins
PAD_DEG = 0.0015  # covers the rounding of the cache key (0.001 deg => at most 0.0007 deg off-centre)
# SIMBAD searches by J2000 position, the image is centred on the observation epoch. A star can have moved
# up to PM_MAX * years since then (Barnard's Star, the fastest known, is 10.4 arcsec/yr: ~6 arcmin by 2026,
# more than the 3.5 arcmin radius of a 5 arcmin patch), so the cone is widened by that much and each row is
# kept only if ITS OWN proper motion could bring it into the image (see build_adql).
PM_MAX_MAS_YR = 11_000.0

FallbackReasonCode = str  # "simbad_unavailable" | "simbad_rate_limited"


class SimbadUnavailable(RuntimeError):
    """SIMBAD timed out, answered an error or something that is not a TAP table."""


class SimbadRateLimited(RuntimeError):
    """The global queries-per-second cap towards SIMBAD is exhausted (no request was sent)."""


@dataclass(frozen=True)
class SimbadRow:
    main_id: str
    otype: str
    otype_label: str
    ra: float          # deg, ICRS, epoch J2000
    dec: float
    pmra: float | None  # mas/yr, mu_alpha* (cos(dec) included)
    pmdec: float | None
    nbref: int
    mag: float | None
    mag_band: str | None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SimbadRow":
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__})  # type: ignore[arg-type]


def build_adql(ra_deg: float, dec_deg: float, radius_deg: float, type_filter: TypeFilter, limit: int,
               years: float = 0.0) -> str:
    """Cone search with the geometric containment predicate, most-cited first.

    `years` = time between J2000 and the image date. When > 0 the cone is widened by PM_MAX * years and an
    object outside the plain cone is kept only if distance <= radius + its own |proper motion| * years.
    """
    centre = f"{ra_deg:.6f}, {dec_deg:.6f}"
    point = "POINT('ICRS', b.ra, b.dec)"
    base = f"CONTAINS({point}, CIRCLE('ICRS', {centre}, {radius_deg:.6f})) = 1"
    if years > 0:
        wide = radius_deg + PM_MAX_MAS_YR / 3.6e6 * years
        reach = (f"DISTANCE({point}, POINT('ICRS', {centre})) <= {radius_deg:.6f} + "
                 f"SQRT(POWER(b.pmra, 2) + POWER(b.pmdec, 2)) / 3600000.0 * {years:.4f}")
        where = [f"CONTAINS({point}, CIRCLE('ICRS', {centre}, {wide:.6f})) = 1", f"({base} OR {reach})"]
    else:
        where = [base]
    extra = adql_condition(type_filter)
    if extra:
        where.append(extra)
    flux_cols = ", ".join(f"f.{band}" for band in MAG_BANDS)
    return (
        f"SELECT TOP {int(limit)} b.main_id, b.otype, o.label AS otype_label, b.ra, b.dec, "
        f"b.pmra, b.pmdec, b.nbref, {flux_cols}\n"
        "FROM basic AS b\n"
        "LEFT JOIN otypedef AS o ON b.otype = o.otype\n"
        "LEFT JOIN allfluxes AS f ON b.oid = f.oidref\n"
        f"WHERE {' AND '.join(where)}\n"
        "ORDER BY b.nbref DESC"
    )


def parse_tap_json(payload: Any) -> list[SimbadRow]:
    """TAP JSON ({"metadata": [{"name": ...}], "data": [[...]]}) -> rows. Anything else raises."""
    try:
        names = [str(c["name"]).lower() for c in payload["metadata"]]
        data = payload["data"]
    except (KeyError, TypeError) as exc:
        raise SimbadUnavailable("SIMBAD answered something that is not a TAP table") from exc
    rows: list[SimbadRow] = []
    for record in data:
        r = dict(zip(names, record, strict=False))
        if r.get("ra") is None or r.get("dec") is None or not r.get("main_id"):
            continue  # an object without a position cannot be drawn
        mag = band = None
        for b in MAG_BANDS:
            if r.get(b.lower()) is not None:
                mag, band = float(r[b.lower()]), b
                break
        otype = str(r.get("otype") or "")
        rows.append(SimbadRow(
            main_id=" ".join(str(r["main_id"]).split()),  # SIMBAD pads ids with runs of spaces
            otype=otype,
            otype_label=str(r.get("otype_label") or otype),
            ra=float(r["ra"]), dec=float(r["dec"]),
            pmra=None if r.get("pmra") is None else float(r["pmra"]),
            pmdec=None if r.get("pmdec") is None else float(r["pmdec"]),
            nbref=int(r.get("nbref") or 0), mag=mag, mag_band=band,
        ))
    return rows


Transport = Callable[[str, str, float], Any]  # (tap_url, adql, timeout_s) -> parsed JSON


def http_transport(tap_url: str, adql: str, timeout_s: float) -> Any:
    timeout = httpx.Timeout(timeout_s, connect=min(2.0, timeout_s))
    try:
        resp = httpx.post(
            tap_url, timeout=timeout, headers={"User-Agent": "spherex-citizen-science-api"},
            data={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "json", "QUERY": adql},
        )
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, ValueError) as exc:  # timeouts, 4xx/5xx, a non-JSON body
        raise SimbadUnavailable(f"SIMBAD query failed: {type(exc).__name__}") from exc


class TTLCache:
    """In-memory, bounded. `get` honours the TTL; `get_stale` ignores it (fallback use)."""

    def __init__(self, ttl_s: float, max_entries: int, clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl_s, self.max_entries, self._clock = ttl_s, max_entries, clock
        self._items: OrderedDict[tuple, tuple[float, list[SimbadRow]]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: tuple) -> list[SimbadRow] | None:
        with self._lock:
            item = self._items.get(key)
            if item is None or self._clock() - item[0] >= self.ttl_s:
                return None
            self._items.move_to_end(key)
            return item[1]

    def get_stale(self, key: tuple) -> list[SimbadRow] | None:
        with self._lock:
            item = self._items.get(key)
            return None if item is None else item[1]

    def put(self, key: tuple, rows: list[SimbadRow]) -> None:
        with self._lock:
            self._items[key] = (self._clock(), rows)
            self._items.move_to_end(key)
            while len(self._items) > self.max_entries:
                self._items.popitem(last=False)


def quantize_years(years: float) -> float:
    """Years since J2000 rounded UP to a quarter of a year: the query is a superset for every date in it."""
    return max(0.0, math.ceil(years * 4.0) / 4.0)


def cache_key(ra_deg: float, dec_deg: float, radius_deg: float, type_filter: str, years: float = 0.0) -> tuple:
    return (round(ra_deg % 360.0, 3) % 360.0, round(dec_deg, 3), round(radius_deg, 3), type_filter,
            quantize_years(years))


def round_center(ra_deg: float, dec_deg: float) -> tuple[float, float]:
    """The centre actually queried: the one in the cache key, so a cache hit is exact."""
    return round(ra_deg % 360.0, 3) % 360.0, round(dec_deg, 3)


@dataclass(frozen=True)
class Lookup:
    rows: list[SimbadRow]
    source: str                      # "simbad" | "cache" | "precomputed" | "none"
    fallback_reason: str | None      # set when SIMBAD could not be used


class SimbadService:
    def __init__(self, cfg: SimbadSettings | None = None, transport: Transport | None = None,
                 cache: TTLCache | None = None, gate: SlidingWindowLimiter | None = None,
                 precomputed: Callable[[str], list[SimbadRow] | None] | None = None) -> None:
        self.cfg = cfg or SimbadSettings()
        self.transport = transport or http_transport
        self.cache = cache or TTLCache(self.cfg.cache_ttl_s, self.cfg.cache_max_entries)
        self.gate = gate or SlidingWindowLimiter(self.cfg.global_qps, 1.0)
        self.precomputed = precomputed or (lambda key: None)

    def query(self, ra_deg: float, dec_deg: float, radius_deg: float,
              type_filter: TypeFilter, years: float = 0.0) -> list[SimbadRow]:
        """One live query (global cap applied). Raises SimbadRateLimited or SimbadUnavailable."""
        if self.gate.check("simbad") is not None:
            raise SimbadRateLimited("global SIMBAD rate cap reached")
        adql = build_adql(ra_deg, dec_deg, radius_deg + PAD_DEG, type_filter, self.cfg.fetch_limit,
                          quantize_years(years))
        return parse_tap_json(self.transport(self.cfg.tap_url, adql, self.cfg.timeout_s))

    def lookup(self, ra_deg: float, dec_deg: float, radius_deg: float, type_filter: TypeFilter,
               years: float = 0.0, golden_key: str | None = None) -> Lookup:
        key = cache_key(ra_deg, dec_deg, radius_deg, type_filter, years)
        fresh = self.cache.get(key)
        if fresh is not None:
            return Lookup(fresh, "cache", None)
        ra_q, dec_q = round_center(ra_deg, dec_deg)
        try:
            rows = self.query(ra_q, dec_q, round(radius_deg, 3), type_filter, years)
        except (SimbadUnavailable, SimbadRateLimited) as exc:
            reason = "simbad_rate_limited" if isinstance(exc, SimbadRateLimited) else "simbad_unavailable"
            logger.warning("SIMBAD not used (%s): %s", reason, exc)
            stale = self.cache.get_stale(key)
            if stale is not None:
                return Lookup(stale, "cache", reason)
            if golden_key:
                pre = self.precomputed(golden_key)
                if pre is not None:
                    return Lookup([r for r in pre if matches(type_filter, r.otype)], "precomputed", reason)
            return Lookup([], "none", reason)
        self.cache.put(key, rows)
        return Lookup(rows, "simbad", None)


def rows_to_dicts(rows: list[SimbadRow]) -> list[dict[str, Any]]:
    return [asdict(r) for r in rows]


@lru_cache
def get_simbad_service() -> SimbadService:
    from app.services.simbad_precomputed import load_precomputed
    return SimbadService(precomputed=load_precomputed)
