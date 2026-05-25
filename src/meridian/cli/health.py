"""Healthcheck CLI: prove Postgres+TimescaleDB and Redis are reachable."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable
from typing import cast

import click

from meridian.bus.redis import client_context
from meridian.config import Settings, get_settings
from meridian.db.postgres import pool_context
from meridian.logging import configure_logging, get_logger


async def check_postgres(settings: Settings) -> tuple[bool, dict[str, str]]:
    """Connect to Postgres and verify the timescaledb extension is enabled."""
    info: dict[str, str] = {}
    try:
        async with pool_context(settings) as pool, pool.acquire() as conn:
            pg_version = await conn.fetchval("SHOW server_version;")
            info["postgres_version"] = str(pg_version)
            ts_version = await conn.fetchval(
                "SELECT extversion FROM pg_extension WHERE extname = 'timescaledb';"
            )
            if ts_version is None:
                info["timescaledb"] = "NOT INSTALLED"
                return False, info
            info["timescaledb_version"] = str(ts_version)
    except Exception as exc:
        info["error"] = f"{type(exc).__name__}: {exc}"
        return False, info
    return True, info


async def check_redis(settings: Settings) -> tuple[bool, dict[str, str]]:
    """Connect to Redis and PING."""
    info: dict[str, str] = {}
    try:
        async with client_context(settings) as client:
            # redis-py types ping/info as `Awaitable[T] | T` (sync/async union);
            # the async client always returns the awaitable.
            pong = await cast(Awaitable[bool], client.ping())
            if not pong:
                info["error"] = "PING returned falsy"
                return False, info
            server_info = await cast(Awaitable[dict[str, object]], client.info(section="server"))
            info["redis_version"] = str(server_info.get("redis_version", "?"))
    except Exception as exc:
        info["error"] = f"{type(exc).__name__}: {exc}"
        return False, info
    return True, info


async def run_health_checks() -> int:
    settings = get_settings()
    configure_logging(settings)
    log = get_logger("meridian.health")

    log.info("healthcheck.start", env=settings.env)

    pg_ok, pg_info = await check_postgres(settings)
    log.info("healthcheck.postgres", ok=pg_ok, **pg_info)

    redis_ok, redis_info = await check_redis(settings)
    log.info("healthcheck.redis", ok=redis_ok, **redis_info)

    overall_ok = pg_ok and redis_ok
    log.info("healthcheck.done", ok=overall_ok)
    return 0 if overall_ok else 1


@click.command(name="health")
def health() -> None:
    """Verify Postgres+TimescaleDB and Redis are reachable."""
    exit_code = asyncio.run(run_health_checks())
    sys.exit(exit_code)
