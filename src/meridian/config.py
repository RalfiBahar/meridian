"""Application configuration loaded from environment variables."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

KalshiEnv = Literal["demo", "prod"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="MERIDIAN_",
        extra="ignore",
    )

    env: Literal["dev", "test", "prod"] = "dev"
    log_level: Literal["debug", "info", "warning", "error"] = "info"

    postgres_dsn: str = Field(
        default="postgresql://meridian:meridian@localhost:5433/meridian",
    )
    redis_url: str = Field(default="redis://localhost:6380/0")

    # Kalshi
    kalshi_env: KalshiEnv = "demo"
    kalshi_access_key: str | None = None
    kalshi_private_key_path: Path | None = None
    kalshi_request_timeout: float = Field(default=10.0, gt=0)


def get_settings() -> Settings:
    return Settings()
