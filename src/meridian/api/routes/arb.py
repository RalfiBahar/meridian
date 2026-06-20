"""Arb violations REST endpoint and live WebSocket feed (Phase 7).

REST
  GET  /api/v1/arb/violations   — current partition + cross-venue arb violations

WebSocket
  WS   /ws/arb                  — periodic arb-violation updates (every 30 s)
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime

import asyncpg
from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect

from meridian.api.auth import get_valid_keys, require_api_key
from meridian.api.deps import get_pool
from meridian.api.models import ArbViolationsResponse, CrossVenueDivergence, PartitionViolation

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _fetch_arb(pool: asyncpg.Pool) -> ArbViolationsResponse:
    from decimal import Decimal

    from meridian.analytics.arb import run_cross_venue_monitor, run_partition_monitor

    partition = await run_partition_monitor(pool, threshold_bps=Decimal("0"), write_signals=False)
    cross = await run_cross_venue_monitor(pool, threshold_bps=Decimal("0"), write_signals=False)

    pv = [
        PartitionViolation(
            group_id=r.group_id,
            group_label=r.group_label,
            n_contracts=r.n_contracts,
            violation_bps=float(r.violation_bps),
            direction=r.direction,
            depth_feasible=r.depth_feasible,
            min_ask_sum=float(r.min_ask_sum),
            max_bid_sum=float(r.max_bid_sum),
        )
        for r in partition
    ]
    cv = [
        CrossVenueDivergence(
            group_id=r.group_id,
            venue_a=r.venue_a,
            p_mid_a=float(r.p_mid_a),
            venue_b=r.venue_b,
            p_mid_b=float(r.p_mid_b),
            divergence_bps=float(r.divergence_bps),
        )
        for r in cross
    ]
    return ArbViolationsResponse(
        partition_violations=pv,
        cross_venue_divergences=cv,
        checked_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# REST endpoint
# ---------------------------------------------------------------------------


@router.get("/arb/violations", response_model=ArbViolationsResponse)
async def arb_violations(
    pool: asyncpg.Pool = Depends(get_pool),
    _auth: str = Depends(require_api_key),
) -> ArbViolationsResponse:
    """Return current no-arbitrage violations across all market groups."""
    return await _fetch_arb(pool)


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------


@router.websocket("/ws/arb")
async def ws_arb(
    websocket: WebSocket,
    api_key: str | None = Query(None),
    pool: asyncpg.Pool = Depends(get_pool),
) -> None:
    """Push arb violation snapshots every 30 seconds."""
    valid = get_valid_keys()
    if valid and (api_key is None or api_key not in valid):
        await websocket.close(code=4003)
        return

    await websocket.accept()
    try:
        while True:
            data = await _fetch_arb(pool)
            payload = {"type": "arb_snapshot", "data": data.model_dump(mode="json")}
            await websocket.send_json(payload)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
    except (WebSocketDisconnect, RuntimeError):
        pass
