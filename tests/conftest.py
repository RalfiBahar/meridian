"""Shared pytest fixtures."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from meridian.config import Settings


@pytest.fixture
def settings() -> Iterator[Settings]:
    """Settings instance for tests. Honors .env and process env."""
    yield Settings()
