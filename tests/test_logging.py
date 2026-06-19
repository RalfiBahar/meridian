"""Unit tests for meridian.logging — structlog configuration."""

from __future__ import annotations

from pytest import MonkeyPatch

from meridian.config import Settings
from meridian.logging import configure_logging, get_logger


def test_configure_logging_dev_mode(monkeypatch: MonkeyPatch) -> None:
    """configure_logging in dev env uses ConsoleRenderer (no crash)."""
    monkeypatch.setenv("MERIDIAN_ENV", "dev")
    monkeypatch.setenv("MERIDIAN_LOG_LEVEL", "info")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    configure_logging(s)  # should not raise


def test_configure_logging_prod_mode(monkeypatch: MonkeyPatch) -> None:
    """configure_logging in prod env uses JSONRenderer (no crash)."""
    monkeypatch.setenv("MERIDIAN_ENV", "prod")
    monkeypatch.setenv("MERIDIAN_LOG_LEVEL", "warning")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    configure_logging(s)  # should not raise


def test_get_logger_returns_bound_logger() -> None:
    """get_logger returns a usable bound logger."""
    log = get_logger("meridian.test")
    assert log is not None


def test_get_logger_no_name() -> None:
    """get_logger without a name also returns a valid logger."""
    log = get_logger()
    assert log is not None
