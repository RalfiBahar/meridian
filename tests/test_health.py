"""Integration tests against the live docker-compose stack.

Run with `make test-all` after `make up`. CI runs these in the integration-smoke job.
"""

from __future__ import annotations

import pytest

from meridian.cli.health import check_postgres, check_redis
from meridian.config import Settings


@pytest.mark.integration
async def test_postgres_reachable() -> None:
    settings = Settings()
    ok, info = await check_postgres(settings)
    assert ok, f"postgres check failed: {info}"
    assert "timescaledb_version" in info, info


@pytest.mark.integration
async def test_redis_reachable() -> None:
    settings = Settings()
    ok, info = await check_redis(settings)
    assert ok, f"redis check failed: {info}"
    assert "redis_version" in info, info
