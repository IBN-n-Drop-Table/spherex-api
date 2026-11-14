"""Citizen discovery reports: storage with a graceful in-memory fallback.

`ReportService` is what the router talks to. It uses Supabase when
SUPABASE_URL / SUPABASE_KEY are set and the table answers. Otherwise it uses
an in-memory store seeded with sample discoveries. If Supabase fails mid-demo,
the service logs a warning and stays on memory until the process restarts, so
the feed never flips between two stores. Reports written to memory are lost
on restart.
"""

import logging
import threading
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any, Literal, Protocol
from uuid import UUID, uuid4

from app.core.config import settings
from app.core.targets import CATALOG
from app.schemas.reports import CitizenReport, CitizenReportCreate, Classification
from app.services.golden_path import normalize_target

logger = logging.getLogger(__name__)

Backend = Literal["supabase", "memory"]


def canonical_target_name(name: str) -> str:
    """'wise_0855' -> 'WISE 0855' for catalogue targets; other names are kept as given."""
    target = CATALOG.get(normalize_target(name))
    return target.name if target else name.strip()


class ReportRepository(Protocol):
    def create(self, report: CitizenReportCreate) -> CitizenReport: ...

    def list_recent(
        self, *, limit: int, target_name: str | None, classification: Classification | None
    ) -> list[CitizenReport]: ...

    def verify(self, report_id: UUID) -> CitizenReport | None: ...


# --------------------------------------------------------------------------- #
# In-memory store
# --------------------------------------------------------------------------- #


def _seed_reports() -> list[CitizenReport]:
    wise, barnard = CATALOG["WISE_0855"], CATALOG["BARNARDS_STAR"]
    return [
        CitizenReport(
            id=UUID("5e1f0855-0000-4000-8000-000000000001"),
            created_at=datetime(2026, 11, 13, 21, 4, tzinfo=UTC),
            discoverer="cosmic_ana",
            notes=(
                "Faint red source at the patch centre, about 2 arcmin west of the 2010 catalogue "
                "position: the ~8 arcsec/yr proper motion of the coldest known brown dwarf."
            ),
            # Epoch-1 (Nov 2025) position = patch centre; see wise_0855.json patch.wcs.
            ra_deg=133.760005,
            dec_deg=-7.242258,
            target_name=wise.name,
            pixel_x=300,
            pixel_y=300,
            classification=Classification.BROWN_DWARF,
            snapshot_url="http://localhost:8000/static/golden_path/wise0855_diff.png",
            verifications_count=4,
        ),
        CitizenReport(
            id=UUID("5e1fba2d-0000-4000-8000-000000000002"),
            created_at=datetime(2026, 11, 13, 18, 37, tzinfo=UTC),
            discoverer="leo_nightwatch",
            notes=(
                "Bright star leaves a dipole in the difference image: it moved north by "
                "about 10 arcsec/yr. Barnard's Star, fastest proper motion in the sky."
            ),
            ra_deg=barnard.ra_deg,
            dec_deg=barnard.dec_deg,
            target_name=barnard.name,
            classification=Classification.HIGH_PROPER_MOTION_STAR,
            verifications_count=2,
        ),
    ]


class InMemoryReportRepository:
    """Thread-safe dict store. FastAPI runs sync endpoints in a thread pool."""

    def __init__(self, seed: list[CitizenReport] | None = None) -> None:
        self._lock = threading.Lock()
        self._reports: dict[UUID, CitizenReport] = {r.id: r for r in (seed or [])}

    def create(self, report: CitizenReportCreate) -> CitizenReport:
        stored = CitizenReport(
            **report.model_dump(),
            id=uuid4(),
            created_at=datetime.now(UTC),
            verifications_count=1,
        )
        with self._lock:
            self._reports[stored.id] = stored
        return stored

    def list_recent(
        self, *, limit: int, target_name: str | None, classification: Classification | None
    ) -> list[CitizenReport]:
        with self._lock:
            reports = list(self._reports.values())
        matches = [
            r
            for r in reports
            if (target_name is None or r.target_name == target_name)
            and (classification is None or r.classification == classification)
        ]
        matches.sort(key=lambda r: r.created_at, reverse=True)
        return matches[:limit]

    def verify(self, report_id: UUID) -> CitizenReport | None:
        with self._lock:
            current = self._reports.get(report_id)
            if current is None:
                return None
            updated = current.model_copy(
                update={"verifications_count": current.verifications_count + 1}
            )
            self._reports[report_id] = updated
            return updated


# --------------------------------------------------------------------------- #
# Supabase store
# --------------------------------------------------------------------------- #


class SupabaseReportRepository:
    """PostgREST access to `citizen_reports`. Verification uses the
    `verify_citizen_report` RPC so the increment is atomic in Postgres."""

    def __init__(self, url: str, key: str, table: str, timeout_s: float) -> None:
        from supabase import ClientOptions, create_client  # optional dependency

        self._client = create_client(
            url,
            key,
            options=ClientOptions(
                postgrest_client_timeout=timeout_s,
                postgrest_client_retry=False,  # fail fast, then fall back
                auto_refresh_token=False,
                persist_session=False,
            ),
        )
        self._table = table

    def ping(self) -> None:
        """Raise if the table cannot be read (bad URL/key, table missing, offline)."""
        self._client.table(self._table).select("id").limit(1).execute()

    def create(self, report: CitizenReportCreate) -> CitizenReport:
        row = report.model_dump(mode="json")
        res = self._client.table(self._table).insert(row).execute()
        return CitizenReport.model_validate(res.data[0])

    def list_recent(
        self, *, limit: int, target_name: str | None, classification: Classification | None
    ) -> list[CitizenReport]:
        query = self._client.table(self._table).select("*")
        if target_name is not None:
            query = query.eq("target_name", target_name)
        if classification is not None:
            query = query.eq("classification", classification.value)
        res = query.order("created_at", desc=True).limit(limit).execute()
        return [CitizenReport.model_validate(row) for row in res.data]

    def verify(self, report_id: UUID) -> CitizenReport | None:
        res = self._client.rpc("verify_citizen_report", {"report_id": str(report_id)}).execute()
        rows: list[dict[str, Any]] = res.data or []
        return CitizenReport.model_validate(rows[0]) if rows else None


# --------------------------------------------------------------------------- #
# Facade with fallback
# --------------------------------------------------------------------------- #


class ReportService:
    def __init__(self, primary: ReportRepository | None, fallback: ReportRepository) -> None:
        self._primary = primary
        self._fallback = fallback
        self._lock = threading.Lock()

    @property
    def backend(self) -> Backend:
        return "supabase" if self._primary is not None else "memory"

    def _call(self, op: str, *args: Any, **kwargs: Any) -> Any:
        primary = self._primary
        if primary is not None:
            try:
                return getattr(primary, op)(*args, **kwargs)
            except Exception as exc:
                self._degrade(f"{op} failed: {exc!r}")
        return getattr(self._fallback, op)(*args, **kwargs)

    def _degrade(self, reason: str) -> None:
        with self._lock:
            if self._primary is not None:
                self._primary = None
                logger.warning(
                    "Supabase unavailable (%s). Citizen reports now use the in-memory store "
                    "until restart.",
                    reason,
                )

    def submit(self, report: CitizenReportCreate) -> CitizenReport:
        if report.target_name is not None:
            report = report.model_copy(
                update={"target_name": canonical_target_name(report.target_name)}
            )
        return self._call("create", report)

    def feed(
        self,
        *,
        limit: int,
        target_name: str | None = None,
        classification: Classification | None = None,
    ) -> list[CitizenReport]:
        if target_name is not None:
            target_name = canonical_target_name(target_name)
        return self._call(
            "list_recent", limit=limit, target_name=target_name, classification=classification
        )

    def verify(self, report_id: UUID) -> CitizenReport | None:
        return self._call("verify", report_id)


def build_report_service() -> ReportService:
    fallback = InMemoryReportRepository(seed=_seed_reports())
    if not (settings.supabase_url and settings.supabase_key):
        logger.warning(
            "SUPABASE_URL/SUPABASE_KEY not set: citizen reports use the in-memory store "
            "(seeded with %d sample discoveries).",
            len(_seed_reports()),
        )
        return ReportService(primary=None, fallback=fallback)

    try:
        primary = SupabaseReportRepository(
            settings.supabase_url,
            settings.supabase_key,
            settings.reports_table,
            settings.supabase_timeout_s,
        )
        primary.ping()
    except Exception as exc:
        logger.warning(
            "Supabase not reachable (%r): citizen reports use the in-memory store.", exc
        )
        return ReportService(primary=None, fallback=fallback)

    logger.info("Citizen reports stored in Supabase table %r.", settings.reports_table)
    return ReportService(primary=primary, fallback=fallback)


@lru_cache
def get_report_service() -> ReportService:
    """FastAPI dependency (override it in tests via app.dependency_overrides)."""
    return build_report_service()
