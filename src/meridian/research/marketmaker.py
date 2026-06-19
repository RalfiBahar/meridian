"""Simulated market maker: inventory management against historical tick data.

Runs an event-driven market-making backtest using quote and trade ticks
from the `ticks` hypertable.

Strategy:
  - Post a bid at mid - half_spread and an ask at mid + half_spread.
  - Sizing is `base_size` normally; halved when |inventory| > 50 % of
    max_inventory (skew threshold); the over-inventoried side is suppressed
    entirely when |inventory| >= max_inventory.
  - Fill rule: a historical trade that crosses our posted quote is counted
    as a fill at our posted price (price improvement for the market maker).
  - Realized P&L: cash flows from completed buy-sell pairs (FIFO).
  - MTM P&L: remaining inventory valued at current midprice minus average
    cost of open lots.
  - Sharpe: annualised (x sqrt(252)) from daily P&L increments when >= 2 days
    of data exist; None otherwise.

This is a simulation only — it does not send orders.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import asyncpg

_DEFAULT_WINDOW = timedelta(days=7)
_ZERO = Decimal("0")


# ---------------------------------------------------------------------------
# Configuration and result types
# ---------------------------------------------------------------------------


@dataclass
class MarketMakerConfig:
    """Strategy parameters for the market-making simulation."""

    half_spread: Decimal = Decimal("0.01")
    base_size: Decimal = Decimal("10")
    max_inventory: Decimal = Decimal("100")
    # skew_threshold: fraction of max_inventory at which we halve size
    skew_threshold: Decimal = Decimal("0.5")


@dataclass
class Fill:
    """A simulated fill against one of our posted quotes."""

    ts: datetime
    side: str  # 'buy' or 'sell' — from the market maker's perspective
    price: Decimal
    size: Decimal


@dataclass
class MarketMakerResult:
    """Backtest results for a single market."""

    market_id: UUID
    config: MarketMakerConfig
    window: timedelta
    n_quote_ticks: int
    n_trade_ticks: int
    n_fills: int
    fill_rate: float  # fills / n_trade_ticks (0.0 when no trades)
    realized_pnl: Decimal
    mtm_pnl: Decimal  # open inventory at final midprice
    total_pnl: Decimal
    final_inventory: Decimal
    max_inventory_reached: Decimal
    sharpe: float | None  # annualised Sharpe of daily P&L; None if < 2 days
    fills: list[Fill] = field(default_factory=list)
    pnl_series: list[tuple[datetime, Decimal]] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            "Market-Making Backtest",
            f"Market:          {self.market_id}",
            f"Window:          {self.window.days} days",
            f"Half-spread:     {self.config.half_spread}",
            f"Base size:       {self.config.base_size}",
            f"Max inventory:   {self.config.max_inventory}",
            f"Quote ticks:     {self.n_quote_ticks}",
            f"Trade ticks:     {self.n_trade_ticks}",
            f"Fills:           {self.n_fills}",
            f"Fill rate:       {self.fill_rate:.1%}",
            f"Realized P&L:    {float(self.realized_pnl):.4f}",
            f"MTM P&L:         {float(self.mtm_pnl):.4f}",
            f"Total P&L:       {float(self.total_pnl):.4f}",
            f"Final inventory: {self.final_inventory}",
            f"Max |inventory|: {self.max_inventory_reached}",
        ]
        if self.sharpe is not None:
            lines.append(f"Sharpe (ann):    {self.sharpe:.3f}")
        else:
            lines.append("Sharpe (ann):    n/a (< 2 days of data)")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def run_mm_backtest(
    pool: asyncpg.Pool,
    market_id: UUID,
    *,
    config: MarketMakerConfig | None = None,
    window: timedelta = _DEFAULT_WINDOW,
) -> MarketMakerResult | None:
    """Run a market-making backtest against historical ticks.

    Returns None when the market has no quote ticks in the window.
    """
    if config is None:
        config = MarketMakerConfig()
    since = datetime.now(tz=UTC) - window
    quotes, trades = await _fetch_ticks(pool, market_id, since)
    if not quotes:
        return None
    return _simulate_mm(market_id, config, window, quotes, trades)


def _simulate_mm(
    market_id: UUID,
    config: MarketMakerConfig,
    window: timedelta,
    quotes: list[dict[str, Any]],
    trades: list[dict[str, Any]],
) -> MarketMakerResult:
    """Pure event-driven simulation — no I/O.

    Processes a merged, time-ordered stream of quote and trade ticks.
    Quote ticks update our posted bid/ask; trade ticks may generate fills.
    """
    # Merge and sort by event_ts ascending.
    events: list[dict[str, Any]] = sorted(
        [{"_kind": "quote", **q} for q in quotes] + [{"_kind": "trade", **t} for t in trades],
        key=lambda e: e["event_ts"],
    )

    inventory = _ZERO
    # Open lots for FIFO P&L: list of (price, size) for open long positions
    open_longs: list[tuple[Decimal, Decimal]] = []
    realized_pnl = _ZERO
    cash_flow = _ZERO  # positive = net receipts
    max_inv = _ZERO

    # Current posted quotes (None = not posting on that side)
    our_bid: Decimal | None = None
    our_ask: Decimal | None = None
    mid: Decimal | None = None

    fills: list[Fill] = []
    pnl_series: list[tuple[datetime, Decimal]] = []
    # Track daily P&L for Sharpe
    daily_pnl: dict[str, Decimal] = {}  # date_str → cumulative P&L that day

    for evt in events:
        ts: datetime = evt["event_ts"]

        if evt["_kind"] == "quote":
            bid_raw = evt.get("bid")
            ask_raw = evt.get("ask")
            if bid_raw is None or ask_raw is None:
                continue
            bid = Decimal(str(bid_raw))
            ask = Decimal(str(ask_raw))
            mid = (bid + ask) / 2

            # Determine size based on inventory skew.
            skew_limit = config.max_inventory * config.skew_threshold
            abs_inv = abs(inventory)
            long_too_much = inventory >= config.max_inventory
            short_too_much = inventory <= -config.max_inventory

            if abs_inv > skew_limit:
                # Reduce size on the over-inventoried side to encourage mean-reversion.
                buy_size = _ZERO if long_too_much else config.base_size / 2
                sell_size = _ZERO if short_too_much else config.base_size / 2
            else:
                buy_size = config.base_size
                sell_size = config.base_size

            our_bid = mid - config.half_spread if buy_size > _ZERO else None
            our_ask = mid + config.half_spread if sell_size > _ZERO else None

        else:  # trade
            trade_price_raw = evt.get("trade_price")
            trade_size_raw = evt.get("trade_size")
            if trade_price_raw is None or (our_bid is None and our_ask is None):
                continue

            trade_price = Decimal(str(trade_price_raw))
            trade_size = Decimal(str(trade_size_raw)) if trade_size_raw is not None else _ZERO
            if trade_size <= _ZERO:
                continue

            # Check for fill on our bid (someone hit our bid → we buy).
            skew_limit = config.max_inventory * config.skew_threshold
            normal_size = config.base_size if abs(inventory) <= skew_limit else config.base_size / 2
            if our_bid is not None and trade_price <= our_bid:
                fill_size = min(trade_size, normal_size)
                if fill_size > _ZERO:
                    f = Fill(ts=ts, side="buy", price=our_bid, size=fill_size)
                    fills.append(f)
                    inventory += fill_size
                    cash_flow -= our_bid * fill_size
                    open_longs.append((our_bid, fill_size))

            # Check for fill on our ask (someone lifted our ask → we sell).
            elif our_ask is not None and trade_price >= our_ask:
                fill_size = min(trade_size, normal_size)
                if fill_size > _ZERO:
                    f = Fill(ts=ts, side="sell", price=our_ask, size=fill_size)
                    fills.append(f)
                    inventory -= fill_size
                    cash_flow += our_ask * fill_size
                    realized_pnl += _realize_pnl(open_longs, our_ask, fill_size)

            if abs(inventory) > max_inv:
                max_inv = abs(inventory)

            # Record P&L snapshot after each trade.
            if mid is not None:
                mtm = inventory * mid
                total = cash_flow + mtm
                pnl_series.append((ts, total))
                day = ts.date().isoformat()
                daily_pnl[day] = total

    # Final MTM using the last midprice.
    mtm_pnl = inventory * mid if mid is not None else _ZERO
    total_pnl = cash_flow + mtm_pnl

    # Annualised Sharpe from daily P&L increments.
    daily_vals = [float(v) for v in daily_pnl.values()]
    sharpe: float | None = None
    if len(daily_vals) >= 2:
        daily_returns = [daily_vals[i] - daily_vals[i - 1] for i in range(1, len(daily_vals))]
        std = statistics.stdev(daily_returns)
        if std > 0:
            sharpe = (statistics.mean(daily_returns) / std) * (252**0.5)

    fill_rate = len(fills) / len(trades) if trades else 0.0

    return MarketMakerResult(
        market_id=market_id,
        config=config,
        window=window,
        n_quote_ticks=len(quotes),
        n_trade_ticks=len(trades),
        n_fills=len(fills),
        fill_rate=fill_rate,
        realized_pnl=realized_pnl,
        mtm_pnl=mtm_pnl,
        total_pnl=total_pnl,
        final_inventory=inventory,
        max_inventory_reached=max_inv,
        sharpe=sharpe,
        fills=fills,
        pnl_series=pnl_series,
    )


# ---------------------------------------------------------------------------
# FIFO P&L helper
# ---------------------------------------------------------------------------


def _realize_pnl(
    open_longs: list[tuple[Decimal, Decimal]],
    sell_price: Decimal,
    sell_size: Decimal,
) -> Decimal:
    """Close sell_size worth of open long lots FIFO; return realized P&L."""
    pnl = _ZERO
    remaining = sell_size
    while remaining > _ZERO and open_longs:
        lot_price, lot_size = open_longs[0]
        close = min(remaining, lot_size)
        pnl += close * (sell_price - lot_price)
        remaining -= close
        if close >= lot_size:
            open_longs.pop(0)
        else:
            open_longs[0] = (lot_price, lot_size - close)
    return pnl


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def _fetch_ticks(
    pool: asyncpg.Pool,
    market_id: UUID,
    since: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (quote_ticks, trade_ticks) for the market since `since`, oldest first."""
    async with pool.acquire() as conn:
        quote_rows = await conn.fetch(
            """
            SELECT event_ts, bid, ask, bid_size, ask_size
            FROM   ticks
            WHERE  market_id = $1
              AND  kind      = 'quote'
              AND  event_ts  >= $2
            ORDER BY event_ts ASC
            """,
            market_id,
            since,
        )
        trade_rows = await conn.fetch(
            """
            SELECT event_ts, trade_price, trade_size, aggressor
            FROM   ticks
            WHERE  market_id = $1
              AND  kind      = 'trade'
              AND  event_ts  >= $2
            ORDER BY event_ts ASC
            """,
            market_id,
            since,
        )
    return [dict(r) for r in quote_rows], [dict(r) for r in trade_rows]
