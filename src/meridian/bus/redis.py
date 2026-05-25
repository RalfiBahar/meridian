"""Redis client factory. Streams API usage lives in Phase 1."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import redis.asyncio as aioredis

from meridian.config import Settings


def create_client(settings: Settings) -> aioredis.Redis:
    """Create an async Redis client. Caller owns `await client.aclose()`."""
    return aioredis.from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=True,
    )


@asynccontextmanager
async def client_context(settings: Settings) -> AsyncIterator[aioredis.Redis]:
    """Open a client for the duration of an async block."""
    client = create_client(settings)
    try:
        yield client
    finally:
        await client.aclose()
