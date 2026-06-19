"""Unit tests for research.marketmaker — simulated market-making backtest.

All tests exercise pure functions (_simulate_mm, _realize_pnl) or use
in-process mock pools; no Docker required.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from meridian.research.marketmaker import (
    MarketMakerConfig,
    MarketMakerResult,
    _realize_pnl,
    _simulate_mm,
    run_mm_backtest,
)

_MID = UUID("00000000-0000-0000-0000-000000000099")
_T0 = datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Mock pool
# ---------------------------------------------------------------------------


class _MockConn:
    def __init__(
        self,
        quote_rows: list[dict[str, Any]] | None = None,
        trade_rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self._quote_rows = quote_rows or []
        self._trade_rows = trade_rows or []
        self._call_count = 0

    async def fetch(self, query: str, *args: object) -> list[dict[str, Any]]:
        self._call_count += 1
        if "kind      = 'quote'" in query:
            return self._quote_rows
        return self._trade_rows


class _MockPool:
    def __init__(self, conn: _MockConn) -> None:
        self._conn = conn

    @asynccontextmanager
    async def acquire(self) -> Any:
        yield self._conn


# ---------------------------------------------------------------------------
# Helpers for building synthetic tick dicts
# ---------------------------------------------------------------------------


def _quote(ts: datetime, bid: str, ask: str) -> dict[str, Any]:
    return {"event_ts": ts, "bid": bid, "ask": ask, "bid_size": "100", "ask_size": "100"}


def _trade(ts: datetime, price: str, size: str = "5") -> dict[str, Any]:
    return {"event_ts": ts, "trade_price": price, "trade_size": size, "aggressor": None}


_DEFAULT_CONFIG = MarketMakerConfig(
    half_spread=Decimal("0.01"),
    base_size=Decimal("10"),
    max_inventory=Decimal("100"),
    skew_threshold=Decimal("0.5"),
)

# ---------------------------------------------------------------------------
# _realize_pnl — pure function
# ---------------------------------------------------------------------------


def test_realize_pnl_simple_round_trip() -> None:
    lots = [(Decimal("0.48"), Decimal("10"))]
    pnl = _realize_pnl(lots, Decimal("0.50"), Decimal("10"))
    # bought at 0.48, sold at 0.50 → P&L = 10 * 0.02 = 0.20
    assert abs(float(pnl) - 0.20) < 1e-9
    assert lots == []  # lot fully consumed


def test_realize_pnl_partial_fill() -> None:
    lots = [(Decimal("0.48"), Decimal("10"))]
    pnl = _realize_pnl(lots, Decimal("0.52"), Decimal("5"))
    # closed 5 of 10 contracts → P&L = 5 * 0.04 = 0.20
    assert abs(float(pnl) - 0.20) < 1e-9
    assert len(lots) == 1
    assert lots[0][1] == Decimal("5")  # 5 remaining


def test_realize_pnl_fifo_multiple_lots() -> None:
    lots = [(Decimal("0.45"), Decimal("3")), (Decimal("0.50"), Decimal("7"))]
    pnl = _realize_pnl(lots, Decimal("0.55"), Decimal("10"))
    # lot1: 3 * (0.55 - 0.45) = 0.30; lot2: 7 * (0.55 - 0.50) = 0.35
    expected = Decimal("3") * Decimal("0.10") + Decimal("7") * Decimal("0.05")
    assert abs(float(pnl - expected)) < 1e-9
    assert lots == []


def test_realize_pnl_empty_lots() -> None:
    lots: list[tuple[Decimal, Decimal]] = []
    pnl = _realize_pnl(lots, Decimal("0.50"), Decimal("10"))
    assert pnl == Decimal("0")


# ---------------------------------------------------------------------------
# _simulate_mm — event-driven pure simulation
# ---------------------------------------------------------------------------


def test_simulate_no_trades_no_fills() -> None:
    quotes = [_quote(_T0, "0.48", "0.52")]
    result = _simulate_mm(_MID, _DEFAULT_CONFIG, timedelta(days=1), quotes, [])
    assert result.n_fills == 0
    assert result.final_inventory == Decimal("0")
    assert result.total_pnl == Decimal("0")
    assert result.fill_rate == 0.0


def test_simulate_trade_lifts_ask_generates_sell_fill() -> None:
    """A trade at our ask price means someone buys from us → we sell (inventory decreases)."""
    t1 = _T0
    t2 = _T0.replace(second=1)
    quotes = [_quote(t1, "0.48", "0.52")]
    # Trade at 0.53 >= our ask (0.49 = 0.50 - 0.01 ... wait, mid = (0.48+0.52)/2 = 0.50
    # our_ask = 0.50 + 0.01 = 0.51
    trades = [_trade(t2, "0.51")]  # at exactly our ask
    result = _simulate_mm(_MID, _DEFAULT_CONFIG, timedelta(days=1), quotes, trades)
    assert result.n_fills == 1
    sell_fill = result.fills[0]
    assert sell_fill.side == "sell"
    assert sell_fill.price == Decimal("0.51")
    # Inventory goes to -5 (we sold 5 contracts)
    assert result.final_inventory == Decimal("-5")


def test_simulate_trade_hits_bid_generates_buy_fill() -> None:
    """A trade at or below our bid means we buy (inventory increases)."""
    t1 = _T0
    t2 = _T0.replace(second=1)
    quotes = [_quote(t1, "0.48", "0.52")]
    # mid = 0.50, our_bid = 0.49
    trades = [_trade(t2, "0.49")]  # at exactly our bid
    result = _simulate_mm(_MID, _DEFAULT_CONFIG, timedelta(days=1), quotes, trades)
    assert result.n_fills == 1
    buy_fill = result.fills[0]
    assert buy_fill.side == "buy"
    assert buy_fill.price == Decimal("0.49")
    assert result.final_inventory == Decimal("5")


def test_simulate_midprice_trade_no_fill() -> None:
    """A trade at the midprice doesn't cross either of our quotes."""
    t1 = _T0
    t2 = _T0.replace(second=1)
    quotes = [_quote(t1, "0.48", "0.52")]
    # mid = 0.50 — below our ask (0.51) and above our bid (0.49) → no fill
    trades = [_trade(t2, "0.50")]
    result = _simulate_mm(_MID, _DEFAULT_CONFIG, timedelta(days=1), quotes, trades)
    assert result.n_fills == 0


def test_simulate_round_trip_positive_pnl() -> None:
    """Buy at our bid then sell at our ask → positive realized P&L."""
    t1 = _T0
    t2 = _T0.replace(second=1)
    t3 = _T0.replace(second=2)
    quotes = [_quote(t1, "0.48", "0.52")]
    # Trade 1 hits our bid (0.49) → buy fill
    # Trade 2 lifts our ask (0.51) → sell fill
    trades = [
        _trade(t2, "0.49"),  # bid fill → buy 5 at 0.49
        _trade(t3, "0.51"),  # ask fill → sell 5 at 0.51
    ]
    result = _simulate_mm(_MID, _DEFAULT_CONFIG, timedelta(days=1), quotes, trades)
    assert result.n_fills == 2
    assert result.final_inventory == Decimal("0")
    # P&L = 5 * (0.51 - 0.49) = 0.10
    assert abs(float(result.realized_pnl) - 0.10) < 1e-9
    assert result.total_pnl > Decimal("0")


def test_simulate_fill_rate_calculation() -> None:
    t1 = _T0
    quotes = [_quote(t1, "0.48", "0.52")]
    # 3 trades: 2 cross our ask, 1 at mid (no fill)
    trades = [
        _trade(t1.replace(second=1), "0.51"),  # fill
        _trade(t1.replace(second=2), "0.50"),  # no fill
        _trade(t1.replace(second=3), "0.51"),  # fill
    ]
    result = _simulate_mm(_MID, _DEFAULT_CONFIG, timedelta(days=1), quotes, trades)
    assert result.n_fills == 2
    assert abs(result.fill_rate - 2 / 3) < 1e-9


def test_simulate_inventory_suppresses_buy_side() -> None:
    """When long inventory hits max, buy quoting is suppressed after the next quote update."""
    config = MarketMakerConfig(
        half_spread=Decimal("0.01"),
        base_size=Decimal("10"),
        max_inventory=Decimal("20"),
        skew_threshold=Decimal("0.5"),
    )
    t1 = _T0
    quotes = [
        _quote(t1, "0.48", "0.52"),  # second=0 → our_bid=0.49 with full size
        _quote(t1.replace(second=5), "0.48", "0.52"),  # second=5 → re-evaluate: inv=20 ≥ max
    ]
    # Use trade_size="20" so fill_size = min(20, base_size=10) = 10 per fill.
    # Two fills → inventory = 20 = max_inventory.
    # After the second quote (second=5), long_too_much=True → our_bid=None.
    # The third trade (second=6) finds our_bid=None → no fill.
    trades = [
        _trade(t1.replace(second=1), "0.49", "20"),  # buy 10 → inv=10
        _trade(t1.replace(second=2), "0.49", "20"),  # buy 10 → inv=20 (at max)
        _trade(t1.replace(second=6), "0.49", "20"),  # suppressed: our_bid=None after quote at 5
    ]
    result = _simulate_mm(_MID, config, timedelta(days=1), quotes, trades)
    assert result.n_fills == 2
    assert result.final_inventory == Decimal("20")


def test_simulate_max_inventory_tracked() -> None:
    config = MarketMakerConfig(
        half_spread=Decimal("0.01"),
        base_size=Decimal("10"),
        max_inventory=Decimal("100"),
    )
    quotes = [_quote(_T0, "0.48", "0.52")]
    # Use trade_size="10" so fill_size = min(10, base_size=10) = 10 per fill.
    # 5 fills * 10 = 50 total inventory; max_inventory_reached = 50.
    trades = [_trade(_T0.replace(second=i), "0.49", "10") for i in range(1, 6)]
    result = _simulate_mm(_MID, config, timedelta(days=1), quotes, trades)
    assert result.max_inventory_reached == Decimal("50")


def test_simulate_sharpe_none_with_single_day() -> None:
    quotes = [_quote(_T0, "0.48", "0.52")]
    trades = [_trade(_T0.replace(second=1), "0.51")]
    result = _simulate_mm(_MID, _DEFAULT_CONFIG, timedelta(days=1), quotes, trades)
    # All events on the same day → only 1 daily P&L point → Sharpe is None
    assert result.sharpe is None


# ---------------------------------------------------------------------------
# run_mm_backtest — async mock-pool tests
# ---------------------------------------------------------------------------


async def test_run_mm_backtest_returns_none_when_no_quotes() -> None:
    conn = _MockConn(quote_rows=[], trade_rows=[])
    pool = _MockPool(conn)
    result = await run_mm_backtest(pool, _MID)  # type: ignore[arg-type]
    assert result is None


async def test_run_mm_backtest_returns_result_with_quotes() -> None:
    conn = _MockConn(
        quote_rows=[_quote(_T0, "0.48", "0.52")],
        trade_rows=[_trade(_T0.replace(second=1), "0.51")],
    )
    pool = _MockPool(conn)
    result = await run_mm_backtest(pool, _MID)  # type: ignore[arg-type]
    assert result is not None
    assert result.market_id == _MID
    assert result.n_quote_ticks == 1
    assert result.n_trade_ticks == 1


async def test_run_mm_backtest_custom_config() -> None:
    config = MarketMakerConfig(half_spread=Decimal("0.02"), base_size=Decimal("5"))
    conn = _MockConn(quote_rows=[_quote(_T0, "0.45", "0.55")])
    pool = _MockPool(conn)
    result = await run_mm_backtest(pool, _MID, config=config)  # type: ignore[arg-type]
    assert result is not None
    assert result.config.half_spread == Decimal("0.02")


# ---------------------------------------------------------------------------
# MarketMakerResult.summary
# ---------------------------------------------------------------------------


def test_market_maker_result_summary_format() -> None:
    result = MarketMakerResult(
        market_id=_MID,
        config=_DEFAULT_CONFIG,
        window=timedelta(days=7),
        n_quote_ticks=50,
        n_trade_ticks=20,
        n_fills=8,
        fill_rate=0.4,
        realized_pnl=Decimal("0.50"),
        mtm_pnl=Decimal("0.10"),
        total_pnl=Decimal("0.60"),
        final_inventory=Decimal("5"),
        max_inventory_reached=Decimal("20"),
        sharpe=1.23,
    )
    s = result.summary()
    assert "7 days" in s
    assert "8" in s
    assert "40.0%" in s
    assert "1.230" in s
    assert "0.6000" in s
