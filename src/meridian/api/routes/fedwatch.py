"""FedWatch REST endpoint (Phase 7).

GET /api/v1/fedwatch  — implied Fed-rate PMF for a given FOMC date.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query

from meridian.api.auth import require_api_key
from meridian.api.deps import get_pool
from meridian.api.models import FedPMFModel, FedWatchResponse

router = APIRouter()


@router.get("/fedwatch", response_model=FedWatchResponse)
async def fedwatch(
    fomc_date: date | None = Query(None, description="FOMC meeting date (YYYY-MM-DD)"),
    cme: bool = Query(False, description="Also fetch CME FedWatch comparison"),
    pool: asyncpg.Pool = Depends(get_pool),
    _auth: str = Depends(require_api_key),
) -> FedWatchResponse:
    """Return the implied Fed-funds rate PMF for the given FOMC date.

    Defaults to the nearest upcoming FOMC date if no date is supplied.
    """
    from meridian.analytics.fedwatch import build_kalshi_pmf, fetch_cme_fedwatch

    resolved_date = fomc_date or await _next_kxfed_date(pool)
    if resolved_date is None:
        raise HTTPException(status_code=404, detail="No open KXFED markets found.")

    kalshi_pmf = await build_kalshi_pmf(pool, resolved_date)
    cme_pmf = await fetch_cme_fedwatch(resolved_date) if cme else None

    def _to_model(pmf: Any) -> FedPMFModel:
        return FedPMFModel(
            fomc_date=pmf.fomc_date,
            source=pmf.source,
            expected_rate=pmf.expected_rate(),
            entropy=pmf.entropy(),
            strikes=[float(s) for s in pmf.strikes],
            probabilities=pmf.probabilities,
            raw_p_mid=pmf.raw_p_mid,
        )

    return FedWatchResponse(
        fomc_date=resolved_date,
        kalshi=_to_model(kalshi_pmf) if kalshi_pmf is not None else None,
        cme=_to_model(cme_pmf) if cme_pmf is not None else None,
    )


async def _next_kxfed_date(pool: asyncpg.Pool) -> date | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT DATE(closes_at) AS fomc_date
            FROM markets m
            JOIN venues v ON v.id = m.venue_id
            WHERE v.code = 'kalshi'
              AND m.external_id LIKE 'KXFED-%%'
              AND m.resolution_status = 'open'
              AND m.closes_at > now()
            ORDER BY m.closes_at ASC
            LIMIT 1
            """
        )
    if row is None:
        return None
    result: date = row["fomc_date"]
    return result
