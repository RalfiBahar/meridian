"""Markets REST endpoints and WebSocket live feed (Phase 7).

REST
  GET  /api/v1/markets                  — paginated market scanner with signals
  GET  /api/v1/markets/{market_id}      — deep view: market + all signals + book + ticks

WebSocket
  WS   /ws/markets                      — periodic snapshot (every 5 s)
  WS   /ws/markets/{market_id}          — live tick feed from the EventHub
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect

from meridian.api.auth import get_valid_keys, require_api_key
from meridian.api.deps import get_hub, get_pool
from meridian.api.hub import EventHub
from meridian.api.models import (
    BookLevel,
    MarketDetail,
    MarketSignalsModel,
    MarketsResponse,
    MarketSummary,
    TickRow,
)

router = APIRouter()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SIGNAL_TYPES = (
    "p_bid",
    "p_ask",
    "p_mid",
    "microprice",
    "depth_weighted_prob",
    "effective_spread",
    "obi",
    "kyle_lambda",
    "amihud",
)

_MARKET_LIST_SQL = """
SELECT
    m.id,
    m.external_id,
    v.code       AS venue,
    m.question,
    m.category,
    m.resolution_status,
    m.closes_at,
    s_bid.value  AS p_bid,
    s_ask.value  AS p_ask,
    s_mid.value  AS p_mid,
    s_mc.value   AS microprice
FROM markets m
JOIN venues v ON v.id = m.venue_id
LEFT JOIN LATERAL (
    SELECT value FROM signals WHERE market_id = m.id AND signal_type = 'p_bid'
    ORDER BY event_ts DESC LIMIT 1
) s_bid ON true
LEFT JOIN LATERAL (
    SELECT value FROM signals WHERE market_id = m.id AND signal_type = 'p_ask'
    ORDER BY event_ts DESC LIMIT 1
) s_ask ON true
LEFT JOIN LATERAL (
    SELECT value FROM signals WHERE market_id = m.id AND signal_type = 'p_mid'
    ORDER BY event_ts DESC LIMIT 1
) s_mid ON true
LEFT JOIN LATERAL (
    SELECT value FROM signals WHERE market_id = m.id AND signal_type = 'microprice'
    ORDER BY event_ts DESC LIMIT 1
) s_mc ON true
WHERE m.resolution_status = $1
{category_filter}
ORDER BY m.closes_at ASC NULLS LAST
LIMIT $2 OFFSET $3
"""

_MARKET_COUNT_SQL = """
SELECT COUNT(*) FROM markets m
WHERE resolution_status = $1
{category_filter}
"""


async def _fetch_markets(
    pool: asyncpg.Pool,
    *,
    status: str = "open",
    category: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    cf = "AND m.category = $4" if category is not None else ""
    list_sql = _MARKET_LIST_SQL.format(category_filter=cf)
    count_sql = _MARKET_COUNT_SQL.format(category_filter=cf)
    list_params: list[Any] = [status, limit, offset]
    count_params: list[Any] = [status]
    if category is not None:
        list_params.append(category)
        count_params.append(category)

    async with pool.acquire() as conn:
        rows = await conn.fetch(list_sql, *list_params)
        total: int = await conn.fetchval(count_sql, *count_params) or 0

    return [dict(r) for r in rows], total


def _row_to_summary(r: dict[str, Any]) -> MarketSummary:
    return MarketSummary(
        id=UUID(str(r["id"])),
        external_id=r["external_id"],
        venue=r["venue"],
        question=r["question"],
        category=r["category"],
        resolution_status=r["resolution_status"],
        closes_at=r["closes_at"],
        p_bid=r["p_bid"],
        p_ask=r["p_ask"],
        p_mid=r["p_mid"],
        microprice=r["microprice"],
    )


async def _fetch_market_by_id(pool: asyncpg.Pool, market_id: UUID) -> dict[str, Any] | None:
    sql = """
        SELECT m.id, m.external_id, v.code AS venue, m.question,
               m.category, m.resolution_status, m.closes_at
        FROM markets m
        JOIN venues v ON v.id = m.venue_id
        WHERE m.id = $1
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(sql, market_id)
    return dict(row) if row else None


async def _fetch_signals(pool: asyncpg.Pool, market_id: UUID) -> dict[str, float | None]:
    """Fetch the latest value for each signal type."""
    sql = """
        SELECT DISTINCT ON (signal_type)
            signal_type,
            value
        FROM signals
        WHERE market_id = $1
          AND signal_type = ANY($2)
        ORDER BY signal_type, event_ts DESC
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, market_id, list(_SIGNAL_TYPES))
    return {r["signal_type"]: r["value"] for r in rows}


async def _fetch_book(pool: asyncpg.Pool, market_id: UUID) -> list[dict[str, Any]]:
    sql = """
        SELECT side, level, price, size
        FROM book_snapshots
        WHERE market_id  = $1
          AND sequence_no = (
              SELECT MAX(sequence_no) FROM book_snapshots WHERE market_id = $1
          )
        ORDER BY side, level
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, market_id)
    return [dict(r) for r in rows]


async def _fetch_recent_ticks(
    pool: asyncpg.Pool, market_id: UUID, limit: int = 50
) -> list[dict[str, Any]]:
    sql = """
        SELECT event_ts, sequence_no, kind, bid, ask,
               bid_size, ask_size, trade_price, trade_size
        FROM ticks
        WHERE market_id = $1
        ORDER BY event_ts DESC, sequence_no DESC
        LIMIT $2
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, market_id, limit)
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------


@router.get("/markets", response_model=MarketsResponse)
async def list_markets(
    status: str = Query("open", description="resolution_status filter"),
    category: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    pool: asyncpg.Pool = Depends(get_pool),
    _auth: str = Depends(require_api_key),
) -> MarketsResponse:
    """Paginated market scanner with the latest implied-probability signals."""
    rows, total = await _fetch_markets(
        pool, status=status, category=category, limit=limit, offset=offset
    )
    return MarketsResponse(markets=[_row_to_summary(r) for r in rows], total=total)


@router.get("/markets/{market_id}", response_model=MarketDetail)
async def get_market(
    market_id: UUID,
    tick_limit: int = Query(50, ge=1, le=200),
    pool: asyncpg.Pool = Depends(get_pool),
    _auth: str = Depends(require_api_key),
) -> MarketDetail:
    """Deep view: market metadata, all signals, L2 book, and recent ticks."""
    row = await _fetch_market_by_id(pool, market_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Market {market_id} not found")

    sigs = await _fetch_signals(pool, market_id)
    book_rows = await _fetch_book(pool, market_id)
    tick_rows = await _fetch_recent_ticks(pool, market_id, limit=tick_limit)

    signals = MarketSignalsModel(
        p_bid=sigs.get("p_bid"),
        p_ask=sigs.get("p_ask"),
        p_mid=sigs.get("p_mid"),
        microprice=sigs.get("microprice"),
        depth_weighted_prob=sigs.get("depth_weighted_prob"),
        effective_spread=sigs.get("effective_spread"),
        obi=sigs.get("obi"),
        kyle_lambda=sigs.get("kyle_lambda"),
        amihud=sigs.get("amihud"),
    )
    book = [
        BookLevel(
            side=b["side"],
            level=int(b["level"]),
            price=float(b["price"]),
            size=float(b["size"]),
        )
        for b in book_rows
    ]
    ticks = [
        TickRow(
            event_ts=t["event_ts"],
            sequence_no=int(t["sequence_no"]),
            kind=t["kind"],
            bid=float(t["bid"]) if t["bid"] is not None else None,
            ask=float(t["ask"]) if t["ask"] is not None else None,
            bid_size=float(t["bid_size"]) if t["bid_size"] is not None else None,
            ask_size=float(t["ask_size"]) if t["ask_size"] is not None else None,
            trade_price=float(t["trade_price"]) if t["trade_price"] is not None else None,
            trade_size=float(t["trade_size"]) if t["trade_size"] is not None else None,
        )
        for t in tick_rows
    ]

    return MarketDetail(
        id=UUID(str(row["id"])),
        external_id=row["external_id"],
        venue=row["venue"],
        question=row["question"],
        category=row["category"],
        resolution_status=row["resolution_status"],
        closes_at=row["closes_at"],
        signals=signals,
        book=book,
        recent_ticks=ticks,
    )


# ---------------------------------------------------------------------------
# WebSocket endpoints
# ---------------------------------------------------------------------------


def _ws_authorized(websocket: WebSocket, api_key: str | None) -> bool:
    valid = get_valid_keys()
    return not valid or (api_key is not None and api_key in valid)


@router.websocket("/ws/markets")
async def ws_market_scanner(
    websocket: WebSocket,
    api_key: str | None = Query(None),
    status: str = Query("open"),
    category: str | None = Query(None),
    pool: asyncpg.Pool = Depends(get_pool),
) -> None:
    """Live market scanner — sends a full snapshot every 5 seconds."""
    if not _ws_authorized(websocket, api_key):
        await websocket.close(code=4003)
        return

    await websocket.accept()
    try:
        while True:
            rows, total = await _fetch_markets(
                pool, status=status, category=category, limit=100, offset=0
            )
            payload = {
                "type": "snapshot",
                "total": total,
                "markets": [
                    _row_to_summary(r).model_dump(mode="json") for r in rows
                ],
            }
            await websocket.send_json(payload)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(websocket.receive_text(), timeout=5.0)
    except (WebSocketDisconnect, RuntimeError):
        pass


@router.websocket("/ws/markets/{market_id}")
async def ws_market_ticks(
    market_id: UUID,
    websocket: WebSocket,
    api_key: str | None = Query(None),
    hub: EventHub = Depends(get_hub),
) -> None:
    """Live tick feed for a single market from the Redis EventHub."""
    if not _ws_authorized(websocket, api_key):
        await websocket.close(code=4003)
        return

    await websocket.accept()
    channel = f"market:{market_id}"
    q = hub.subscribe(channel)
    try:
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=30.0)
                await websocket.send_json({"type": "tick", "data": event})
            except TimeoutError:
                await websocket.send_json({"type": "ping"})
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        hub.unsubscribe(channel, q)
