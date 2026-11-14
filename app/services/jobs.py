"""In-memory job store for live (out-of-cache) queries.

* Thread-safe: requests and background tasks touch it from different threads.
* Identical requests share one job (same kind + key) while it is active or
  completed, so a frontend that retries doesn't start a second IRSA run.
  Failed jobs are not reused: asking again retries.
* At most `max_running` jobs run at once (IRSA throttles parallel
  downloads); the rest wait as `queued`. Above `max_active` queued+running
  jobs, new work is refused (`TooManyJobs` -> 503).
* Finished jobs expire `ttl_s` seconds after they finish; `on_expire` lets
  the caller delete their files.
"""

import logging
import re
import threading
import time
from collections.abc import Callable, Hashable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from uuid import UUID, uuid4

from pydantic import BaseModel

from app.core.config import settings
from app.schemas.jobs import JobKind, JobResponse, JobStatus

logger = logging.getLogger(__name__)

ACTIVE = (JobStatus.QUEUED, JobStatus.RUNNING)


class TooManyJobs(RuntimeError):
    pass


def error_message(exc: Exception, limit: int = 300) -> str:
    """One readable line for the frontend: no HTML error pages, no stack dumps."""
    text = re.sub(r"<[^>]+>", " ", str(exc))
    text = " ".join(text.split())
    return f"{type(exc).__name__}: {text}"[:limit]


@dataclass
class Job:
    id: UUID
    kind: JobKind
    key: Hashable
    status: JobStatus = JobStatus.QUEUED
    result: BaseModel | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: float | None = None  # monotonic clock
    stage: str = "queued"
    progress: float = 0.0
    message: str | None = "En cola..."
    started_at: float | None = None  # monotonic clock

    def to_response(self, clock: Callable[[], float] = time.monotonic) -> JobResponse:
        eta = None
        if self.status is JobStatus.RUNNING and self.started_at is not None and self.progress >= 0.06:
            elapsed = clock() - self.started_at
            eta = max(0, round(elapsed * (1 - self.progress) / self.progress))
        return JobResponse(
            job_id=self.id,
            kind=self.kind,
            status=self.status,
            result=self.result,
            error=self.error,
            created_at=self.created_at,
            updated_at=self.updated_at,
            stage=self.stage,
            progress=round(self.progress, 3),
            message=self.message,
            eta_s=eta,
            retry_after_ms=settings.retry_after_ms if self.status in ACTIVE else None,
        )


class JobService:
    def __init__(
        self,
        ttl_s: float,
        max_running: int,
        max_active: int,
        on_expire: Callable[[UUID], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl_s = ttl_s
        self._max_active = max_active
        self._on_expire = on_expire
        self._clock = clock
        self._lock = threading.Lock()
        self._run_slots = threading.Semaphore(max_running)
        self._jobs: dict[UUID, Job] = {}
        self._by_key: dict[tuple[JobKind, Hashable], UUID] = {}

    # -- bookkeeping -------------------------------------------------------- #
    def _purge_locked(self) -> list[UUID]:
        now = self._clock()
        expired = [j.id for j in self._jobs.values()
                   if j.finished_at is not None and now - j.finished_at > self._ttl_s]
        for job_id in expired:
            job = self._jobs.pop(job_id)
            if self._by_key.get((job.kind, job.key)) == job_id:
                del self._by_key[(job.kind, job.key)]
        return expired

    def _expire(self, expired: list[UUID]) -> None:
        if self._on_expire:
            for job_id in expired:
                try:
                    self._on_expire(job_id)
                except Exception:  # cleanup must never break a request
                    logger.exception("cleanup of expired job %s failed", job_id)

    def _update(self, job_id: UUID, **changes) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            for name, value in changes.items():
                setattr(job, name, value)
            job.updated_at = datetime.now(UTC)
            if job.status not in ACTIVE:
                job.finished_at = self._clock()
                job.progress = 1.0 if job.status is JobStatus.COMPLETED else job.progress
                job.stage = "done" if job.status is JobStatus.COMPLETED else "failed"
                if job.status is JobStatus.FAILED:
                    self._by_key.pop((job.kind, job.key), None)

    # -- public API --------------------------------------------------------- #
    def get(self, job_id: UUID) -> JobResponse | None:
        with self._lock:
            expired = self._purge_locked()
            job = self._jobs.get(job_id)
            response = job.to_response(self._clock) if job else None
        self._expire(expired)
        return response

    def report(self, job_id: UUID, stage: str, progress: float, message: str) -> None:
        """Progress callback for running jobs (called from the worker thread)."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status not in ACTIVE:
                return
            job.stage, job.message = stage, message
            job.progress = max(job.progress, min(max(progress, 0.0), 0.999))
            job.updated_at = datetime.now(UTC)

    def add_completed(self, kind: JobKind, key: Hashable, result: BaseModel, message: str) -> JobResponse:
        """Register an already-finished job (cached answer): same polling contract, instant."""
        with self._lock:
            expired = self._purge_locked()
            existing = self._by_key.get((kind, key))
            if existing is not None and self._jobs[existing].status is JobStatus.COMPLETED:
                response = self._jobs[existing].to_response(self._clock)
            else:
                job = Job(id=uuid4(), kind=kind, key=key, status=JobStatus.COMPLETED, result=result,
                          stage="done", progress=1.0, message=message, finished_at=self._clock())
                self._jobs[job.id] = job
                self._by_key[(kind, key)] = job.id
                response = job.to_response(self._clock)
        self._expire(expired)
        return response

    def find_or_create(self, kind: JobKind, key: Hashable) -> tuple[JobResponse, bool]:
        """Return (job, created). Raises TooManyJobs when the queue is full."""
        response: JobResponse | None = None
        created = False
        with self._lock:
            expired = self._purge_locked()
            existing_id = self._by_key.get((kind, key))
            active = sum(1 for j in self._jobs.values() if j.status in ACTIVE)
            if existing_id is not None:
                response = self._jobs[existing_id].to_response(self._clock)
            elif active < self._max_active:
                job = Job(id=uuid4(), kind=kind, key=key)
                self._jobs[job.id] = job
                self._by_key[(kind, key)] = job.id
                response, created = job.to_response(self._clock), True
        self._expire(expired)
        if response is None:
            raise TooManyJobs(f"{active} live jobs already queued or running")
        return response, created

    def run(self, job_id: UUID, work: Callable[[], BaseModel]) -> None:
        """Execute a job. Called from a background task (worker thread)."""
        with self._run_slots:
            self._update(job_id, status=JobStatus.RUNNING, stage="query", started_at=self._clock(),
                         message="Iniciando...")
            try:
                result = work()
            except Exception as exc:
                logger.warning("job %s failed: %s: %s", job_id, type(exc).__name__, exc)
                self._update(job_id, status=JobStatus.FAILED, error=error_message(exc))
            else:
                self._update(job_id, status=JobStatus.COMPLETED, result=result)


def _delete_live_files(job_id: UUID) -> None:
    import shutil

    shutil.rmtree(settings.static_dir / "live" / str(job_id), ignore_errors=True)


@lru_cache
def get_job_service() -> JobService:
    """FastAPI dependency (override it in tests via app.dependency_overrides)."""
    return JobService(
        ttl_s=settings.job_ttl_s,
        max_running=settings.live_max_running_jobs,
        max_active=settings.live_max_active_jobs,
        on_expire=_delete_live_files,
    )
