"""Postgres connection pool factory backed by asyncpg."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg

from meridian.config import Settings


async def create_pool(settings: Settings) -> asyncpg.Pool:
    """Create an asyncpg connection pool. Caller owns `await pool.close()`."""
    pool = await asyncpg.create_pool(
        dsn=settings.postgres_dsn,
        min_size=1,
        max_size=10,
        command_timeout=10.0,
    )
    if pool is None:
        raise RuntimeError("asyncpg.create_pool returned None")
    return pool


@asynccontextmanager
async def pool_context(settings: Settings) -> AsyncIterator[asyncpg.Pool]:
    """Open a pool for the duration of an async block."""
    pool = await create_pool(settings)
    try:
        yield pool
    finally:
        await pool.close()
