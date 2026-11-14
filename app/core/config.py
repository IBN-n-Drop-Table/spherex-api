"""Application settings.

Kept dependency-free (no pydantic-settings) for the MVP. Values can be
overridden through environment variables.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path


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


settings = Settings()
