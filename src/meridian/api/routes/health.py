"""Health endpoint — GET /health (always public)."""

from __future__ import annotations

from typing import Any

import asyncpg
from fastapi import APIRouter, Depends

from meridian.api.deps import get_pool

router = APIRouter()


@router.get("/health")
async def health(pool: asyncpg.Pool = Depends(get_pool)) -> dict[str, Any]:
    """Return {"status": "ok"} when the DB is reachable."""
    try:
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return {"status": "ok"}
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}
