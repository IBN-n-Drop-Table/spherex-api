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

    # Live (out-of-cache) processing: scripts/generate_golden_path.py run as a
    # background job. Needs requirements-science.txt; without it jobs fail
    # with a clear error instead of breaking the API.
    pipeline_script: Path = Path(__file__).resolve().parents[2] / "scripts" / "generate_golden_path.py"
    default_fov_arcmin: float = 5.0
    live_max_exposures_per_epoch: int = field(
        default_factory=lambda: _env_int("LIVE_MAX_EXPOSURES_PER_EPOCH", 24)
    )
    live_download_workers: int = field(default_factory=lambda: _env_int("LIVE_DOWNLOAD_WORKERS", 2))
    live_max_running_jobs: int = field(default_factory=lambda: _env_int("LIVE_MAX_RUNNING_JOBS", 1))
    live_max_active_jobs: int = field(default_factory=lambda: _env_int("LIVE_MAX_ACTIVE_JOBS", 8))
    job_ttl_s: int = field(default_factory=lambda: _env_int("JOB_TTL_S", 3600))


settings = Settings()
