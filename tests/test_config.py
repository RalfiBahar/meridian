"""Settings load correctly from environment."""

from __future__ import annotations

from pytest import MonkeyPatch

from meridian.config import Settings


def test_settings_defaults_have_expected_shape(monkeypatch: MonkeyPatch) -> None:
    for key in (
        "MERIDIAN_ENV",
        "MERIDIAN_LOG_LEVEL",
        "MERIDIAN_POSTGRES_DSN",
        "MERIDIAN_REDIS_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.env in {"dev", "test", "prod"}
    assert s.log_level in {"debug", "info", "warning", "error"}
    assert s.postgres_dsn.startswith("postgresql://")
    assert s.redis_url.startswith("redis://")


def test_settings_reads_env_prefix(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("MERIDIAN_ENV", "test")
    monkeypatch.setenv("MERIDIAN_LOG_LEVEL", "debug")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.env == "test"
    assert s.log_level == "debug"
