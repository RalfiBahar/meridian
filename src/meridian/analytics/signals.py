"""Implied probability signal extraction from ingested ticks.

Reads from `ticks` and `book_snapshots`, computes derived signals, and writes
them to the `signals` table.  Four signals are produced per market per run:

  p_bid     — best bid (latest quote tick)
  p_ask     — best ask (latest quote tick)
  p_mid     — (bid + ask) / 2
  microprice — (bid * ask_size + ask * bid_size) / (bid_size + ask_size)
               requires both sides to have non-zero size; NULL otherwise
  depth_weighted_prob — size-weighted average of bid and ask book levels;
                        requires at least one level on each side in the latest
                        book snapshot; NULL otherwise

All five are written as separate rows in `signals`.  Writes use
`ON CONFLICT DO NOTHING` — re-running the extractor over an already-covered
time window is safe.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import asyncpg

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class MarketSignals:
    """Computed signals for one market at one point in time."""

    market_id: UUID
    event_ts: datetime
    p_bid: Decimal | None
    p_ask: Decimal | None
    p_mid: Decimal | None
    microprice: Decimal | None
    depth_weighted_prob: Decimal | None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def compute_market_signals(
    pool: asyncpg.Pool,
    market_id: UUID,
) -> MarketSignals | None:
    """Compute current implied-probability signals for one market.

    Returns None if no quote tick exists for the market yet.
    """
    quote = await _latest_quote(pool, market_id)
    if quote is None:
        return None

    event_ts = quote["event_ts"]
    bid: Decimal | None = quote["bid"]
    ask: Decimal | None = quote["ask"]
    bid_size: Decimal | None = quote["bid_size"]
    ask_size: Decimal | None = quote["ask_size"]

    p_mid = _midprice(bid, ask)
    micro = _microprice(bid, ask, bid_size, ask_size)
    depth = await _depth_weighted_prob(pool, market_id)

    return MarketSignals(
        market_id=market_id,
        event_ts=event_ts,
        p_bid=bid,
        p_ask=ask,
        p_mid=p_mid,
        microprice=micro,
        depth_weighted_prob=depth,
    )


async def run_signal_sweep(
    pool: asyncpg.Pool,
    *,
    category: str | None = None,
) -> int:
    """Compute and persist signals for every (open) market.

    Skips markets with no quote ticks.  Returns the number of markets processed.
    """
    market_ids = await _open_market_ids(pool, category=category)
    written = 0
    for market_id in market_ids:
        signals = await compute_market_signals(pool, market_id)
        if signals is None:
            continue
        await _write_signals(pool, signals)
        written += 1
    return written


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _latest_quote(
    pool: asyncpg.Pool,
    market_id: UUID,
) -> dict[str, Any] | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT event_ts, bid, ask, bid_size, ask_size
            FROM   ticks
            WHERE  market_id = $1
              AND  kind = 'quote'
            ORDER BY event_ts DESC, sequence_no DESC
            LIMIT 1
            """,
            market_id,
        )
    return dict(row) if row else None


async def _depth_weighted_prob(
    pool: asyncpg.Pool,
    market_id: UUID,
) -> Decimal | None:
    """Size-weighted average of the latest full book snapshot.

    Returns the average of the size-weighted bid and the size-weighted ask.
    Requires at least one level on each side.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT side, price, size
            FROM   book_snapshots
            WHERE  market_id    = $1
              AND  sequence_no  = (
                       SELECT MAX(sequence_no) FROM book_snapshots
                       WHERE  market_id = $1
                   )
            """,
            market_id,
        )

    if not rows:
        return None

    bid_sides = ("bid", "yes")
    ask_sides = ("ask", "no")
    bid_levels = [
        (Decimal(str(r["price"])), Decimal(str(r["size"]))) for r in rows if r["side"] in bid_sides
    ]
    ask_levels = [
        (Decimal(str(r["price"])), Decimal(str(r["size"]))) for r in rows if r["side"] in ask_sides
    ]

    if not bid_levels or not ask_levels:
        return None

    total_bid_size = sum(s for _, s in bid_levels)
    total_ask_size = sum(s for _, s in ask_levels)

    if total_bid_size == 0 or total_ask_size == 0:
        return None

    # Size-weighted average price on each side.
    weighted_bid = sum(p * s for p, s in bid_levels) / total_bid_size
    weighted_ask = sum(p * s for p, s in ask_levels) / total_ask_size

    return (weighted_bid + weighted_ask) / 2


def _midprice(bid: Decimal | None, ask: Decimal | None) -> Decimal | None:
    if bid is None or ask is None:
        return None
    return (bid + ask) / 2


def _microprice(
    bid: Decimal | None,
    ask: Decimal | None,
    bid_size: Decimal | None,
    ask_size: Decimal | None,
) -> Decimal | None:
    """(bid * ask_size + ask * bid_size) / (bid_size + ask_size)."""
    if bid is None or ask is None or bid_size is None or ask_size is None:
        return None
    total = bid_size + ask_size
    if total == 0:
        return None
    return (bid * ask_size + ask * bid_size) / total


async def _open_market_ids(
    pool: asyncpg.Pool,
    *,
    category: str | None,
) -> list[UUID]:
    query = """
        SELECT id FROM markets
        WHERE resolution_status = 'open'
    """
    params: list[Any] = []
    if category is not None:
        query += " AND category = $1"
        params.append(category)
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *params)
    return [UUID(str(r["id"])) for r in rows]


async def _write_signals(pool: asyncpg.Pool, s: MarketSignals) -> None:
    """Insert signal rows; each signal_type gets its own row."""
    signals: list[tuple[str, Decimal | None]] = [
        ("p_bid", s.p_bid),
        ("p_ask", s.p_ask),
        ("p_mid", s.p_mid),
        ("microprice", s.microprice),
        ("depth_weighted_prob", s.depth_weighted_prob),
    ]
    now = datetime.now(tz=UTC)
    records = [
        (
            s.event_ts,
            s.market_id,
            signal_type,
            float(value) if value is not None else None,
            json.dumps({}),
            now,
        )
        for signal_type, value in signals
        if value is not None
    ]
    if not records:
        return
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO signals (event_ts, market_id, signal_type, value, metadata, ingest_ts)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6)
            ON CONFLICT DO NOTHING
            """,
            records,
        )
