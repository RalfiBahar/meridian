"""Application configuration loaded from environment variables."""

from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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


def get_settings() -> Settings:
    return Settings()
