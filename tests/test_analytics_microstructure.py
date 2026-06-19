"""Unit tests for analytics.microstructure — pure functions only.

No DB required: all functions that touch DB are tested via mock pools in
`test_analytics_signals.py` style; the pure computations are tested directly
here since they don't need any fixture.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest

from meridian.analytics.microstructure import (
    ExecutionEstimate,
    MicrostructureMetrics,
    _amihud_ratio,
    _effective_spread,
    _kyle_lambda,
    _open_market_ids,
    _order_book_imbalance,
    _quote_ticks,
    _trade_ticks,
    _write_signals,
    compute_microstructure,
    run_microstructure_sweep,
    simulate_execution,
)

_MARKET_ID = UUID("00000000-0000-0000-0000-000000000002")
_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Mock pool for simulate_execution
# ---------------------------------------------------------------------------


class _MockConn:
    def __init__(self, book_rows: list[dict[str, Any]]) -> None:
        self._book = book_rows

    async def fetch(self, query: str, *args: object) -> list[dict[str, Any]]:
        return self._book

    async def fetchrow(self, query: str, *args: object) -> dict[str, Any] | None:
        return self._book[0] if self._book else None


class _MockPool:
    def __init__(self, conn: _MockConn) -> None:
        self._conn = conn

    @asynccontextmanager
    async def acquire(self) -> Any:
        yield self._conn


# ---------------------------------------------------------------------------
# _effective_spread
# ---------------------------------------------------------------------------


def test_effective_spread_basic() -> None:
    quotes = [
        {"bid": "0.48", "ask": "0.52"},
        {"bid": "0.47", "ask": "0.53"},
    ]
    result = _effective_spread(quotes)
    assert result is not None
    # mean of 0.04 and 0.06 = 0.05
    assert abs(float(result) - 0.05) < 1e-10


def test_effective_spread_none_when_empty() -> None:
    assert _effective_spread([]) is None


def test_effective_spread_skips_null_fields() -> None:
    quotes = [{"bid": None, "ask": "0.52"}, {"bid": "0.48", "ask": None}]
    assert _effective_spread(quotes) is None


# ---------------------------------------------------------------------------
# _order_book_imbalance
# ---------------------------------------------------------------------------


def test_obi_positive_when_more_bids() -> None:
    quotes = [{"bid_size": "300", "ask_size": "100"}]
    r = _order_book_imbalance(quotes)
    assert r is not None
    # (300 - 100) / 400 = 0.5
    assert abs(float(r) - 0.5) < 1e-10


def test_obi_negative_when_more_asks() -> None:
    quotes = [{"bid_size": "100", "ask_size": "300"}]
    r = _order_book_imbalance(quotes)
    assert r is not None
    assert abs(float(r) - (-0.5)) < 1e-10


def test_obi_none_when_zero_total() -> None:
    quotes = [{"bid_size": "0", "ask_size": "0"}]
    assert _order_book_imbalance(quotes) is None


def test_obi_none_when_empty() -> None:
    assert _order_book_imbalance([]) is None


def test_obi_uses_first_non_null_row() -> None:
    quotes = [
        {"bid_size": None, "ask_size": None},
        {"bid_size": "200", "ask_size": "200"},
    ]
    r = _order_book_imbalance(quotes)  # type: ignore[arg-type]
    assert r is not None
    assert float(r) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# _amihud_ratio
# ---------------------------------------------------------------------------


def test_amihud_none_when_fewer_than_two_trades() -> None:
    assert _amihud_ratio([]) is None
    assert _amihud_ratio([{"trade_price": "0.50", "trade_size": "100"}]) is None


def test_amihud_finite_and_positive() -> None:
    trades = [
        {"trade_price": "0.50", "trade_size": "100"},
        {"trade_price": "0.52", "trade_size": "50"},
        {"trade_price": "0.51", "trade_size": "200"},
    ]
    r = _amihud_ratio(trades)
    assert r is not None
    assert r > 0
    assert float("inf") != r


def test_amihud_zero_price_skipped() -> None:
    trades = [
        {"trade_price": "0.00", "trade_size": "100"},  # skipped (division by 0)
        {"trade_price": "0.50", "trade_size": "100"},
        {"trade_price": "0.55", "trade_size": "50"},
    ]
    r = _amihud_ratio(trades)
    # Should not crash; may return None or a value
    assert r is None or r >= 0


# ---------------------------------------------------------------------------
# _kyle_lambda
# ---------------------------------------------------------------------------


def test_kyle_lambda_none_when_insufficient_data() -> None:
    assert _kyle_lambda([], []) is None
    quotes_only = [{"event_ts": _NOW, "bid": "0.48", "ask": "0.52"}]
    assert _kyle_lambda(quotes_only, []) is None


def test_kyle_lambda_returns_float() -> None:
    from datetime import timedelta

    quotes = [
        {
            "event_ts": _NOW + timedelta(seconds=i),
            "bid": str(0.48 + i * 0.001),
            "ask": str(0.52 + i * 0.001),
        }
        for i in range(5)
    ]
    trades = [
        {"aggressor": "buy", "trade_size": "10"},
        {"aggressor": "sell", "trade_size": "5"},
        {"aggressor": "buy", "trade_size": "20"},
        {"aggressor": "sell", "trade_size": "8"},
    ]
    r = _kyle_lambda(quotes, trades)
    # Should return a finite float (slope coefficient).
    assert r is not None
    assert isinstance(r, float)
    assert r == r  # not NaN


# ---------------------------------------------------------------------------
# simulate_execution (via mock pool)
# ---------------------------------------------------------------------------


async def test_simulate_buy_single_level() -> None:
    book = [
        {"side": "ask", "price": "0.52", "size": "200"},
    ]
    pool = _MockPool(_MockConn(book))
    est = await simulate_execution(
        pool,
        _MARKET_ID,
        side="buy",
        target_quantity=Decimal("100"),  # type: ignore[arg-type]
    )
    assert isinstance(est, ExecutionEstimate)
    assert est.filled_quantity == Decimal("100")
    assert est.avg_fill_price == Decimal("0.52")
    assert est.slippage == Decimal("0")  # filled at best quote
    assert not est.partially_filled


async def test_simulate_buy_across_levels() -> None:
    book = [
        {"side": "ask", "price": "0.52", "size": "50"},
        {"side": "ask", "price": "0.54", "size": "100"},
    ]
    pool = _MockPool(_MockConn(book))
    est = await simulate_execution(
        pool,
        _MARKET_ID,
        side="buy",
        target_quantity=Decimal("100"),  # type: ignore[arg-type]
    )
    assert est.filled_quantity == Decimal("100")
    # 50 @ 0.52 + 50 @ 0.54 = (26 + 27) / 100 = 0.53
    assert est.avg_fill_price is not None
    assert abs(float(est.avg_fill_price) - 0.53) < 1e-9
    assert est.slippage is not None and est.slippage > 0  # paid more than best ask
    assert est.n_levels_consumed == 2


async def test_simulate_sell_empty_book() -> None:
    pool = _MockPool(_MockConn([]))
    est = await simulate_execution(
        pool,
        _MARKET_ID,
        side="sell",
        target_quantity=Decimal("100"),  # type: ignore[arg-type]
    )
    assert est.filled_quantity == Decimal("0")
    assert est.partially_filled


async def test_simulate_partial_fill() -> None:
    book = [{"side": "ask", "price": "0.52", "size": "30"}]
    pool = _MockPool(_MockConn(book))
    est = await simulate_execution(
        pool,
        _MARKET_ID,
        side="buy",
        target_quantity=Decimal("100"),  # type: ignore[arg-type]
    )
    assert est.filled_quantity == Decimal("30")
    assert est.partially_filled


async def test_simulate_invalid_side() -> None:
    pool = _MockPool(_MockConn([]))
    with pytest.raises(ValueError, match="side must be"):
        await simulate_execution(pool, _MARKET_ID, side="lateral", target_quantity=Decimal("10"))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# MicrostructureMetrics is a plain dataclass (no DB needed)
# ---------------------------------------------------------------------------


def test_metrics_dataclass() -> None:
    m = MicrostructureMetrics(
        market_id=_MARKET_ID,
        window=timedelta(days=7),
        effective_spread=Decimal("0.04"),
        obi=Decimal("0.2"),
        kyle_lambda=0.001,
        amihud=0.00005,
        n_quotes=100,
        n_trades=30,
    )
    assert m.n_quotes == 100
    assert m.effective_spread == Decimal("0.04")


# ---------------------------------------------------------------------------
# DB helper tests
# ---------------------------------------------------------------------------


class _FetchConn:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    async def fetch(self, query: str, *args: object) -> list[dict[str, Any]]:
        return self._rows

    async def executemany(self, query: str, records: object) -> None:
        pass


class _FetchPool:
    def __init__(self, conn: _FetchConn) -> None:
        self._conn = conn

    @asynccontextmanager
    async def acquire(self) -> Any:
        yield self._conn


async def test_quote_ticks_returns_rows() -> None:
    row: dict[str, Any] = {
        "event_ts": _NOW,
        "bid": "0.48",
        "ask": "0.52",
        "bid_size": "100",
        "ask_size": "80",
        "aggressor": None,
    }
    pool = _FetchPool(_FetchConn([row]))
    result = await _quote_ticks(pool, _MARKET_ID, _NOW)  # type: ignore[arg-type]
    assert len(result) == 1
    assert result[0]["bid"] == "0.48"


async def test_trade_ticks_returns_rows() -> None:
    row: dict[str, Any] = {
        "event_ts": _NOW,
        "trade_price": "0.50",
        "trade_size": "200",
        "aggressor": "buy",
    }
    pool = _FetchPool(_FetchConn([row]))
    result = await _trade_ticks(pool, _MARKET_ID, _NOW)  # type: ignore[arg-type]
    assert len(result) == 1
    assert result[0]["trade_price"] == "0.50"


async def test_open_market_ids_returns_uuids() -> None:
    rows: list[dict[str, Any]] = [{"id": str(_MARKET_ID)}]
    pool = _FetchPool(_FetchConn(rows))
    result = await _open_market_ids(pool)  # type: ignore[arg-type]
    assert result == [_MARKET_ID]


async def test_write_signals_calls_executemany() -> None:
    """_write_signals inserts records when metrics are non-None."""
    calls: list[list[Any]] = []

    class _TrackConn:
        async def executemany(self, query: str, records: object) -> None:
            calls.append(list(records))  # type: ignore[call-overload]

    class _TrackPool:
        @asynccontextmanager
        async def acquire(self) -> Any:
            yield _TrackConn()

    m = MicrostructureMetrics(
        market_id=_MARKET_ID,
        window=timedelta(days=7),
        effective_spread=Decimal("0.04"),
        obi=Decimal("0.1"),
        kyle_lambda=0.001,
        amihud=0.00002,
        n_quotes=10,
        n_trades=5,
    )
    await _write_signals(_TrackPool(), m)  # type: ignore[arg-type]
    assert len(calls) == 1
    assert len(calls[0]) == 4  # effective_spread, obi, kyle_lambda, amihud


async def test_write_signals_no_call_when_all_none() -> None:
    """_write_signals skips DB when all metrics are None."""
    calls: list[Any] = []

    class _TrackConn:
        async def executemany(self, query: str, records: object) -> None:
            calls.append(records)

    class _TrackPool:
        @asynccontextmanager
        async def acquire(self) -> Any:
            yield _TrackConn()

    m = MicrostructureMetrics(
        market_id=_MARKET_ID,
        window=timedelta(days=7),
        effective_spread=None,
        obi=None,
        kyle_lambda=None,
        amihud=None,
        n_quotes=0,
        n_trades=0,
    )
    await _write_signals(_TrackPool(), m)  # type: ignore[arg-type]
    assert calls == []


# ---------------------------------------------------------------------------
# compute_microstructure via mock pool
# ---------------------------------------------------------------------------


async def test_compute_microstructure_returns_metrics() -> None:
    """compute_microstructure produces a MicrostructureMetrics for a market."""
    quote_row: dict[str, Any] = {
        "event_ts": _NOW,
        "bid": "0.48",
        "ask": "0.52",
        "bid_size": "100",
        "ask_size": "80",
        "aggressor": None,
    }

    call_n = 0

    class _Conn:
        async def fetch(self, query: str, *args: object) -> list[Any]:
            nonlocal call_n
            call_n += 1
            if call_n == 1:
                return [quote_row]  # quote ticks
            return []  # trade ticks

    class _Pool:
        @asynccontextmanager
        async def acquire(self) -> Any:
            yield _Conn()

    result = await compute_microstructure(_Pool(), _MARKET_ID)  # type: ignore[arg-type]
    assert isinstance(result, MicrostructureMetrics)
    assert result.market_id == _MARKET_ID
    assert result.n_quotes == 1
    assert result.n_trades == 0


# ---------------------------------------------------------------------------
# run_microstructure_sweep via mock pool
# ---------------------------------------------------------------------------


async def test_run_microstructure_sweep_specific_market() -> None:
    """run_microstructure_sweep with market_id skips _open_market_ids."""
    call_n = 0

    class _Conn:
        async def fetch(self, query: str, *args: object) -> list[Any]:
            nonlocal call_n
            call_n += 1
            if call_n == 1:
                return []  # quote ticks
            return []  # trade ticks

        async def executemany(self, query: str, records: object) -> None:
            pass

    class _Pool:
        @asynccontextmanager
        async def acquire(self) -> Any:
            yield _Conn()

    results = await run_microstructure_sweep(_Pool(), market_id=_MARKET_ID, write_signals=False)  # type: ignore[arg-type]
    assert len(results) == 1
    assert results[0].market_id == _MARKET_ID


async def test_run_microstructure_sweep_all_markets() -> None:
    """run_microstructure_sweep without market_id fetches open markets first."""
    call_n = 0

    class _Conn:
        async def fetch(self, query: str, *args: object) -> list[Any]:
            nonlocal call_n
            call_n += 1
            if call_n == 1:
                return [{"id": str(_MARKET_ID)}]  # _open_market_ids
            if call_n == 2:
                return []  # quote ticks for the market
            return []  # trade ticks

    class _Pool:
        @asynccontextmanager
        async def acquire(self) -> Any:
            yield _Conn()

    results = await run_microstructure_sweep(_Pool(), write_signals=False)  # type: ignore[arg-type]
    assert len(results) == 1


async def test_run_microstructure_sweep_write_signals() -> None:
    """run_microstructure_sweep calls _write_signals when write_signals=True."""
    executemany_calls: list[Any] = []
    call_n = 0

    class _Conn:
        async def fetch(self, query: str, *args: object) -> list[Any]:
            nonlocal call_n
            call_n += 1
            if call_n == 1:
                return []  # quote ticks (no data → all None metrics)
            return []  # trade ticks

        async def executemany(self, query: str, records: object) -> None:
            executemany_calls.append(list(records))  # type: ignore[call-overload]

    class _Pool:
        @asynccontextmanager
        async def acquire(self) -> Any:
            yield _Conn()

    # All metrics None → _write_signals returns early, no executemany call.
    results = await run_microstructure_sweep(_Pool(), market_id=_MARKET_ID, write_signals=True)  # type: ignore[arg-type]
    assert len(results) == 1
    assert executemany_calls == []
