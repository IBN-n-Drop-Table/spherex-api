"""Application settings.

Kept dependency-free (no pydantic-settings) for the MVP. Values can be
overridden through environment variables.

Supabase is optional: when SUPABASE_URL / SUPABASE_KEY are unset, citizen
reports are kept in memory (see app/services/reports.py).
"""

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _env_float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    app_name: str = "SPHEREx Citizen Science API"
    app_version: str = "0.1.0"
    api_v1_prefix: str = "/api/v1"
    cors_origins: list[str] = field(
        default_factory=lambda: _split_csv(os.getenv("CORS_ORIGINS", "*"))
    )
    data_dir: Path = Path(__file__).resolve().parent.parent / "data"
    static_dir: Path = Path(__file__).resolve().parent.parent / "static"
    supabase_url: str | None = field(default_factory=lambda: os.getenv("SUPABASE_URL") or None)
    # anon key is enough (RLS policies in supabase/migrations/); service-role also works.
    supabase_key: str | None = field(default_factory=lambda: os.getenv("SUPABASE_KEY") or None)
    supabase_timeout_s: float = field(
        default_factory=lambda: float(os.getenv("SUPABASE_TIMEOUT_S", "5"))
    )
    reports_table: str = "citizen_reports"
    # In-memory store only: start the feed with 2 labelled demo examples (is_example: true).
    reports_demo_seed: bool = field(default_factory=lambda: os.getenv("REPORTS_DEMO_SEED") == "1")

    # Live (out-of-cache) processing: scripts/generate_golden_path.py run as a
    # background job. Needs requirements-science.txt; without it jobs fail
    # with a clear error instead of breaking the API.
    pipeline_script: Path = Path(__file__).resolve().parents[2] / "scripts" / "generate_golden_path.py"
    default_fov_arcmin: float = 5.0
    # Live mode downloads the minimum: N cutouts per detector and visit.
    live_max_per_detector: int = field(default_factory=lambda: _env_int("LIVE_MAX_PER_DETECTOR", 1))
    live_download_workers: int = field(default_factory=lambda: _env_int("LIVE_DOWNLOAD_WORKERS", 4))
    live_max_fov_arcmin: float = field(default_factory=lambda: _env_float("LIVE_MAX_FOV_ARCMIN", 15.0))
    # Measured on 2026-11-14: one SPHEREx cutout takes ~11-13 s (it always carries
    # the ~4 MB EPSF table). Used only for the ETA shown to users.
    cutout_seconds_estimate: float = field(default_factory=lambda: _env_float("CUTOUT_SECONDS_ESTIMATE", 12.0))
    live_max_running_jobs: int = field(default_factory=lambda: _env_int("LIVE_MAX_RUNNING_JOBS", 1))
    live_max_active_jobs: int = field(default_factory=lambda: _env_int("LIVE_MAX_ACTIVE_JOBS", 8))
    job_ttl_s: int = field(default_factory=lambda: _env_int("JOB_TTL_S", 3600))
    retry_after_ms: int = field(default_factory=lambda: _env_int("JOB_RETRY_AFTER_MS", 1500))

    # Offline-first: IRSA gets this many seconds (total, retries included) to
    # answer the metadata query; past that, or on 429/5xx, cached data is served.
    sia_budget_s: float = field(default_factory=lambda: _env_float("SIA_BUDGET_S", 5.0))

    # Server origin used to build absolute image URLs (e.g. https://spherex-api.onrender.com).
    # Unset: the request's own base URL. A trailing "/static" is accepted and ignored.
    public_base: str | None = field(default_factory=lambda: os.getenv("PUBLIC_BASE") or None)


settings = Settings()
