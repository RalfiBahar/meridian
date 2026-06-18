"""FastAPI dependency injection: DB pool, Redis client, EventHub (Phase 7)."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
from fastapi import FastAPI

from meridian.api.hub import EventHub
from meridian.bus.redis import create_client
from meridian.config import get_settings
from meridian.db.postgres import create_pool


class _AppState:
    pool: asyncpg.Pool
    redis: Any  # redis.asyncio.Redis — stubs omitted in mypy overrides
    hub: EventHub


_state = _AppState()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage the asyncpg pool, Redis client, and EventHub for the API process."""
    settings = get_settings()
    _state.pool = await create_pool(settings)
    _state.redis = create_client(settings)
    _state.hub = EventHub()
    hub_task = asyncio.create_task(_state.hub.run(_state.redis))
    try:
        yield
    finally:
        hub_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await hub_task
        await _state.redis.aclose()
        await _state.pool.close()


def get_pool() -> asyncpg.Pool:
    """FastAPI dependency: shared asyncpg connection pool."""
    return _state.pool


def get_redis() -> Any:
    """FastAPI dependency: shared Redis client."""
    return _state.redis


def get_hub() -> EventHub:
    """FastAPI dependency: shared WebSocket EventHub."""
    return _state.hub
