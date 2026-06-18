"""Microstructure analytics: spread, OBI, Kyle's lambda, Amihud, execution simulator.

All metrics are computed from the `ticks` and `book_snapshots` hypertables.
Results are optionally written to the `signals` table.

Rolling windows are expressed as `timedelta` objects; the query always
orders by `event_ts DESC` so only the most recent data in the window is used.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import asyncpg
import numpy as np

_DEFAULT_WINDOW = timedelta(days=7)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class MicrostructureMetrics:
    """Computed microstructure signals for one market over one window."""

    market_id: UUID
    window: timedelta
    effective_spread: Decimal | None  # ask - bid (cents, annualised as Decimal)
    obi: Decimal | None  # (bid_size - ask_size) / (bid_size + ask_size)
    kyle_lambda: float | None  # slope of ΔP ~ signed_volume
    amihud: float | None  # mean(|return| / volume)
    n_quotes: int
    n_trades: int


@dataclass
class ExecutionEstimate:
    """Simulated fill for a target position against a book snapshot."""

    market_id: UUID
    side: str  # 'buy' or 'sell'
    target_quantity: Decimal
    filled_quantity: Decimal
    avg_fill_price: Decimal | None
    best_quote: Decimal | None
    slippage: Decimal | None  # fill_price - best_quote (positive = costs more)
    n_levels_consumed: int
    partially_filled: bool


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def compute_microstructure(
    pool: asyncpg.Pool,
    market_id: UUID,
    *,
    window: timedelta = _DEFAULT_WINDOW,
) -> MicrostructureMetrics:
    """Compute all microstructure metrics for one market over `window`."""
    since = datetime.now(tz=UTC) - window
    quotes = await _quote_ticks(pool, market_id, since)
    trades = await _trade_ticks(pool, market_id, since)

    spread = _effective_spread(quotes)
    obi = _order_book_imbalance(quotes)
    kyle = _kyle_lambda(quotes, trades)
    amihud = _amihud_ratio(trades)

    return MicrostructureMetrics(
        market_id=market_id,
        window=window,
        effective_spread=spread,
        obi=obi,
        kyle_lambda=kyle,
        amihud=amihud,
        n_quotes=len(quotes),
        n_trades=len(trades),
    )


async def run_microstructure_sweep(
    pool: asyncpg.Pool,
    *,
    market_id: UUID | None = None,
    window: timedelta = _DEFAULT_WINDOW,
    write_signals: bool = True,
) -> list[MicrostructureMetrics]:
    """Run microstructure for one or all open markets."""
    if market_id is not None:
        ids = [market_id]
    else:
        ids = await _open_market_ids(pool)

    results = []
    for mid in ids:
        m = await compute_microstructure(pool, mid, window=window)
        if write_signals:
            await _write_signals(pool, m)
        results.append(m)
    return results


async def simulate_execution(
    pool: asyncpg.Pool,
    market_id: UUID,
    *,
    side: str,
    target_quantity: Decimal,
) -> ExecutionEstimate:
    """Walk the latest book snapshot to estimate fill price and slippage.

    `side` must be 'buy' or 'sell'.  For a buy order, we consume ask levels
    (ascending price); for a sell order, we consume bid levels (descending).
    """
    if side not in ("buy", "sell"):
        raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")

    levels = await _latest_book_levels(pool, market_id, side=side)
    if not levels:
        return ExecutionEstimate(
            market_id=market_id,
            side=side,
            target_quantity=target_quantity,
            filled_quantity=Decimal("0"),
            avg_fill_price=None,
            best_quote=None,
            slippage=None,
            n_levels_consumed=0,
            partially_filled=True,
        )

    best_quote = Decimal(str(levels[0]["price"]))
    remaining = target_quantity
    total_cost = Decimal("0")
    n_levels = 0
    filled = Decimal("0")

    for level in levels:
        if remaining <= 0:
            break
        size = Decimal(str(level["size"]))
        price = Decimal(str(level["price"]))
        fill = min(remaining, size)
        total_cost += fill * price
        filled += fill
        remaining -= fill
        n_levels += 1

    partially_filled = remaining > 0
    avg_fill = total_cost / filled if filled > 0 else None
    slippage = (avg_fill - best_quote) if avg_fill is not None else None
    if side == "sell" and slippage is not None:
        slippage = -slippage  # positive slippage = we got less than best bid

    return ExecutionEstimate(
        market_id=market_id,
        side=side,
        target_quantity=target_quantity,
        filled_quantity=filled,
        avg_fill_price=avg_fill,
        best_quote=best_quote,
        slippage=slippage,
        n_levels_consumed=n_levels,
        partially_filled=partially_filled,
    )


# ---------------------------------------------------------------------------
# Pure microstructure functions
# ---------------------------------------------------------------------------


def _effective_spread(quotes: list[dict[str, Any]]) -> Decimal | None:
    """Mean of (ask - bid) across quote ticks."""
    spreads = [
        Decimal(str(q["ask"])) - Decimal(str(q["bid"]))
        for q in quotes
        if q["bid"] is not None and q["ask"] is not None
    ]
    if not spreads:
        return None
    return sum(spreads, Decimal("0")) / len(spreads)


def _order_book_imbalance(quotes: list[dict[str, Any]]) -> Decimal | None:
    """Latest OBI: (bid_size - ask_size) / (bid_size + ask_size)."""
    for q in quotes:  # already ordered newest first
        bs = q["bid_size"]
        as_ = q["ask_size"]
        if bs is None or as_ is None:
            continue
        bid_size = Decimal(str(bs))
        ask_size = Decimal(str(as_))
        total = bid_size + ask_size
        if total == 0:
            continue
        return (bid_size - ask_size) / total
    return None


def _kyle_lambda(
    quotes: list[dict[str, Any]],
    trades: list[dict[str, Any]],
) -> float | None:
    """OLS of ΔP_mid ~ signed_volume over matched trade/quote pairs.

    For each trade, we use the midprice before and after the trade.
    Requires at least 2 data points for OLS.
    """
    if len(trades) < 2 or len(quotes) < 2:
        return None

    # Build a time-sorted array of (ts, mid_price).
    mid_series = sorted(
        [
            (q["event_ts"], (Decimal(str(q["bid"])) + Decimal(str(q["ask"]))) / 2)
            for q in quotes
            if q["bid"] is not None and q["ask"] is not None
        ],
        key=lambda x: x[0],
    )
    if len(mid_series) < 2:
        return None

    price_arr = np.array([float(m) for _, m in mid_series])
    delta_p = np.diff(price_arr)

    # Signed volume: positive for buyer-initiated, negative for seller.
    signed_vols = []
    for t in trades:
        aggressor = t.get("aggressor")
        size = float(t["trade_size"] or 0)
        if aggressor == "buy":
            signed_vols.append(size)
        elif aggressor == "sell":
            signed_vols.append(-size)
        else:
            signed_vols.append(0.0)

    # Align series to the shorter one.
    n = min(len(delta_p), len(signed_vols))
    if n < 2:
        return None

    x = np.array(signed_vols[:n])
    y = delta_p[:n]

    # OLS: y = alpha + lambda * x
    X = np.column_stack([np.ones(n), x])
    try:
        coeffs, *_ = np.linalg.lstsq(X, y, rcond=None)
    except np.linalg.LinAlgError:
        return None

    return float(coeffs[1])  # lambda


def _amihud_ratio(trades: list[dict[str, Any]]) -> float | None:
    """Mean of |return| / volume across consecutive trades."""
    if len(trades) < 2:
        return None

    prices = [float(t["trade_price"]) for t in trades if t["trade_price"] is not None]
    volumes = [float(t["trade_size"]) for t in trades if t["trade_size"] is not None]

    if len(prices) < 2:
        return None

    ratios = []
    for i in range(1, min(len(prices), len(volumes))):
        if prices[i - 1] == 0 or volumes[i] == 0:
            continue
        ret = abs(prices[i] - prices[i - 1]) / prices[i - 1]
        ratios.append(ret / volumes[i])

    return float(np.mean(ratios)) if ratios else None


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def _quote_ticks(
    pool: asyncpg.Pool,
    market_id: UUID,
    since: datetime,
) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT event_ts, bid, ask, bid_size, ask_size, aggressor
            FROM   ticks
            WHERE  market_id = $1
              AND  kind      = 'quote'
              AND  event_ts  >= $2
            ORDER BY event_ts DESC
            """,
            market_id,
            since,
        )
    return [dict(r) for r in rows]


async def _trade_ticks(
    pool: asyncpg.Pool,
    market_id: UUID,
    since: datetime,
) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT event_ts, trade_price, trade_size, aggressor
            FROM   ticks
            WHERE  market_id = $1
              AND  kind      = 'trade'
              AND  event_ts  >= $2
            ORDER BY event_ts DESC
            """,
            market_id,
            since,
        )
    return [dict(r) for r in rows]


async def _latest_book_levels(
    pool: asyncpg.Pool,
    market_id: UUID,
    *,
    side: str,
) -> list[dict[str, Any]]:
    """Return book levels at the latest snapshot, ordered for a walk."""
    # For a buy order (consuming ask side): ascending price.
    # For a sell order (consuming bid side): descending price.
    db_sides = ("ask", "no") if side == "buy" else ("bid", "yes")
    order = "ASC" if side == "buy" else "DESC"

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT side, price, size
            FROM   book_snapshots
            WHERE  market_id   = $1
              AND  side        = ANY($2::text[])
              AND  sequence_no = (
                SELECT MAX(sequence_no) FROM book_snapshots
                WHERE  market_id = $1
              )
            ORDER BY price {order}
            """,
            market_id,
            list(db_sides),
        )
    return [dict(r) for r in rows]


async def _open_market_ids(pool: asyncpg.Pool) -> list[UUID]:
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT id FROM markets WHERE resolution_status = 'open'")
    return [UUID(str(r["id"])) for r in rows]


async def _write_signals(pool: asyncpg.Pool, m: MicrostructureMetrics) -> None:
    now = datetime.now(tz=UTC)
    window_days = m.window.days
    meta = json.dumps({"window_days": window_days})

    records: list[tuple[Any, ...]] = []
    if m.effective_spread is not None:
        records.append((now, m.market_id, "effective_spread", float(m.effective_spread), meta, now))
    if m.obi is not None:
        records.append((now, m.market_id, "obi", float(m.obi), meta, now))
    if m.kyle_lambda is not None:
        records.append((now, m.market_id, "kyle_lambda", m.kyle_lambda, meta, now))
    if m.amihud is not None:
        records.append((now, m.market_id, "amihud", m.amihud, meta, now))

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
