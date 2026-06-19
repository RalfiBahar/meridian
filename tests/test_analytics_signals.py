"""Unit tests for analytics.signals: implied-probability signal extraction.

Uses mock DB pools — no Docker required.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from meridian.analytics.signals import (
    MarketSignals,
    _depth_weighted_prob,
    _latest_quote,
    _microprice,
    _midprice,
    _open_market_ids,
    _write_signals,
    compute_market_signals,
    run_signal_sweep,
)

# ---------------------------------------------------------------------------
# Mock pool infrastructure
# ---------------------------------------------------------------------------

_MARKET_ID = UUID("00000000-0000-0000-0000-000000000001")
_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


class _MockConn:
    """Conn stub; override fetch_row and fetch per test."""

    def __init__(
        self,
        quote_row: dict[str, Any] | None = None,
        book_rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self._quote = quote_row
        self._book = book_rows or []

    async def fetchrow(self, query: str, *args: object) -> dict[str, Any] | None:
        return self._quote

    async def fetch(self, query: str, *args: object) -> list[dict[str, Any]]:
        return self._book

    async def execute(self, query: str, *args: object) -> str:
        return "OK"

    async def executemany(self, query: str, records: object) -> None:
        pass


class _MockPool:
    def __init__(self, conn: _MockConn) -> None:
        self._conn = conn

    @asynccontextmanager
    async def acquire(self) -> Any:
        yield self._conn


# ---------------------------------------------------------------------------
# Pure function tests
# ---------------------------------------------------------------------------


def test_midprice_both_sides() -> None:
    assert _midprice(Decimal("0.40"), Decimal("0.60")) == Decimal("0.50")


def test_midprice_none_if_missing() -> None:
    assert _midprice(None, Decimal("0.60")) is None
    assert _midprice(Decimal("0.40"), None) is None


def test_microprice_basic() -> None:
    bid, ask = Decimal("0.40"), Decimal("0.60")
    bid_size, ask_size = Decimal("30"), Decimal("10")
    # (0.40 * 10 + 0.60 * 30) / (30 + 10) = (4 + 18) / 40 = 0.55
    result = _microprice(bid, ask, bid_size, ask_size)
    assert result is not None
    assert abs(float(result) - 0.55) < 1e-9


def test_microprice_equal_sizes() -> None:
    # When sizes equal, microprice == midprice.
    bid, ask = Decimal("0.40"), Decimal("0.60")
    size = Decimal("100")
    result = _microprice(bid, ask, size, size)
    assert result is not None
    assert abs(float(result) - 0.50) < 1e-9


def test_microprice_zero_total_returns_none() -> None:
    assert _microprice(Decimal("0.5"), Decimal("0.5"), Decimal("0"), Decimal("0")) is None


def test_microprice_any_none_returns_none() -> None:
    assert _microprice(None, Decimal("0.6"), Decimal("10"), Decimal("10")) is None


# ---------------------------------------------------------------------------
# Async DB-mock tests
# ---------------------------------------------------------------------------


async def test_latest_quote_returns_row() -> None:
    row: dict[str, Any] = {
        "event_ts": _NOW,
        "bid": Decimal("0.48"),
        "ask": Decimal("0.52"),
        "bid_size": Decimal("100"),
        "ask_size": Decimal("80"),
    }
    pool = _MockPool(_MockConn(quote_row=row))
    result = await _latest_quote(pool, _MARKET_ID)  # type: ignore[arg-type]
    assert result is not None
    assert result["bid"] == Decimal("0.48")


async def test_latest_quote_none_when_no_data() -> None:
    pool = _MockPool(_MockConn(quote_row=None))
    result = await _latest_quote(pool, _MARKET_ID)  # type: ignore[arg-type]
    assert result is None


async def test_depth_weighted_prob_bid_ask() -> None:
    book_rows = [
        {"side": "bid", "price": "0.50", "size": "100"},
        {"side": "bid", "price": "0.49", "size": "50"},
        {"side": "ask", "price": "0.52", "size": "100"},
        {"side": "ask", "price": "0.53", "size": "50"},
    ]
    pool = _MockPool(_MockConn(book_rows=book_rows))
    result = await _depth_weighted_prob(pool, _MARKET_ID)  # type: ignore[arg-type]
    assert result is not None
    # weighted_bid = (0.50*100 + 0.49*50) / 150 = (50 + 24.5) / 150 ≈ 0.4967
    # weighted_ask = (0.52*100 + 0.53*50) / 150 = (52 + 26.5) / 150 ≈ 0.5233
    # midpoint ≈ 0.51
    assert 0.50 < float(result) < 0.52


async def test_depth_weighted_prob_yes_no_sides() -> None:
    """Kalshi yes/no sides map correctly to bid/ask for weighting."""
    book_rows = [
        {"side": "yes", "price": "0.55", "size": "200"},
        {"side": "no", "price": "0.45", "size": "200"},
    ]
    pool = _MockPool(_MockConn(book_rows=book_rows))
    result = await _depth_weighted_prob(pool, _MARKET_ID)  # type: ignore[arg-type]
    # weighted_bid (yes) = 0.55, weighted_ask (no) = 0.45 → midpoint = 0.50
    assert result is not None
    assert abs(float(result) - 0.50) < 1e-9


async def test_depth_weighted_prob_none_if_empty() -> None:
    pool = _MockPool(_MockConn(book_rows=[]))
    result = await _depth_weighted_prob(pool, _MARKET_ID)  # type: ignore[arg-type]
    assert result is None


async def test_depth_weighted_prob_none_if_one_side_missing() -> None:
    pool = _MockPool(_MockConn(book_rows=[{"side": "bid", "price": "0.50", "size": "100"}]))
    result = await _depth_weighted_prob(pool, _MARKET_ID)  # type: ignore[arg-type]
    assert result is None


async def test_compute_market_signals_all_fields() -> None:
    quote: dict[str, Any] = {
        "event_ts": _NOW,
        "bid": Decimal("0.48"),
        "ask": Decimal("0.52"),
        "bid_size": Decimal("100"),
        "ask_size": Decimal("100"),
    }
    book: list[dict[str, Any]] = [
        {"side": "bid", "price": "0.48", "size": "100"},
        {"side": "ask", "price": "0.52", "size": "100"},
    ]
    pool = _MockPool(_MockConn(quote_row=quote, book_rows=book))
    result = await compute_market_signals(pool, _MARKET_ID)  # type: ignore[arg-type]
    assert isinstance(result, MarketSignals)
    assert result.p_bid == Decimal("0.48")
    assert result.p_ask == Decimal("0.52")
    assert result.p_mid == Decimal("0.50")
    assert result.microprice is not None
    assert abs(float(result.microprice) - 0.50) < 1e-9  # equal sizes → equal mid
    assert result.depth_weighted_prob is not None


async def test_compute_market_signals_none_if_no_quote() -> None:
    pool = _MockPool(_MockConn(quote_row=None))
    result = await compute_market_signals(pool, _MARKET_ID)  # type: ignore[arg-type]
    assert result is None


async def test_depth_weighted_prob_zero_size_returns_none() -> None:
    """Zero total size on bid side → None (line 173)."""
    book_rows = [
        {"side": "bid", "price": "0.50", "size": "0"},
        {"side": "ask", "price": "0.52", "size": "100"},
    ]
    pool = _MockPool(_MockConn(book_rows=book_rows))
    result = await _depth_weighted_prob(pool, _MARKET_ID)  # type: ignore[arg-type]
    assert result is None


async def test_open_market_ids_no_category() -> None:
    """_open_market_ids fetches all open markets when category is None."""

    class _IdsConn:
        async def fetch(self, query: str, *args: object) -> list[dict[str, Any]]:
            return [{"id": str(_MARKET_ID)}]

    class _IdsPool:
        @asynccontextmanager
        async def acquire(self) -> Any:
            yield _IdsConn()

    result = await _open_market_ids(_IdsPool(), category=None)  # type: ignore[arg-type]
    assert result == [_MARKET_ID]


async def test_open_market_ids_with_category() -> None:
    """_open_market_ids passes the category param to the query."""
    received_args: list[Any] = []

    class _CatConn:
        async def fetch(self, query: str, *args: object) -> list[dict[str, Any]]:
            received_args.extend(args)
            return []

    class _CatPool:
        @asynccontextmanager
        async def acquire(self) -> Any:
            yield _CatConn()

    await _open_market_ids(_CatPool(), category="fed")  # type: ignore[arg-type]
    assert received_args == ["fed"]


async def test_write_signals_calls_executemany() -> None:
    """_write_signals inserts one row per non-None signal value."""

    signals_obj = MarketSignals(
        market_id=_MARKET_ID,
        event_ts=_NOW,
        p_bid=None,
        p_ask=None,
        p_mid=None,
        microprice=None,
        depth_weighted_prob=None,
    )
    # All None → no DB call.
    class _TrackConn:
        def __init__(self) -> None:
            self.calls: list[Any] = []

        async def executemany(self, query: str, records: object) -> None:
            self.calls.append(list(records))  # type: ignore[call-overload]

    class _TrackPool:
        def __init__(self) -> None:
            self.conn = _TrackConn()

        @asynccontextmanager
        async def acquire(self) -> Any:
            yield self.conn

    pool = _TrackPool()
    await _write_signals(pool, signals_obj)  # type: ignore[arg-type]
    assert pool.conn.calls == []  # early return when all None


async def test_write_signals_inserts_present_values() -> None:
    """_write_signals sends executemany with one record per non-None signal."""
    signals_obj = MarketSignals(
        market_id=_MARKET_ID,
        event_ts=_NOW,
        p_bid=Decimal("0.44"),
        p_ask=Decimal("0.46"),
        p_mid=Decimal("0.45"),
        microprice=None,
        depth_weighted_prob=None,
    )

    class _TrackConn:
        def __init__(self) -> None:
            self.calls: list[Any] = []

        async def executemany(self, query: str, records: object) -> None:
            self.calls.append(list(records))  # type: ignore[call-overload]

    class _TrackPool:
        def __init__(self) -> None:
            self.conn = _TrackConn()

        @asynccontextmanager
        async def acquire(self) -> Any:
            yield self.conn

    pool = _TrackPool()
    await _write_signals(pool, signals_obj)  # type: ignore[arg-type]
    assert len(pool.conn.calls) == 1
    assert len(pool.conn.calls[0]) == 3  # p_bid, p_ask, p_mid


async def test_run_signal_sweep_returns_zero_when_no_markets() -> None:
    """run_signal_sweep returns 0 when there are no open markets."""

    class _EmptyConn:
        async def fetch(self, query: str, *args: object) -> list[Any]:
            return []

    class _EmptyPool:
        @asynccontextmanager
        async def acquire(self) -> Any:
            yield _EmptyConn()

    count = await run_signal_sweep(_EmptyPool())  # type: ignore[arg-type]
    assert count == 0


async def test_run_signal_sweep_skips_markets_with_no_quote() -> None:
    """run_signal_sweep skips markets where compute_market_signals returns None."""

    call_n = 0

    class _SkipConn:
        async def fetch(self, query: str, *args: object) -> list[Any]:
            nonlocal call_n
            call_n += 1
            if call_n == 1:
                # _open_market_ids: return one market
                return [{"id": str(_MARKET_ID)}]
            # _latest_quote fetch (via _depth_weighted_prob path) → empty
            return []

        async def fetchrow(self, query: str, *args: object) -> dict[str, Any] | None:
            return None  # no quote → compute_market_signals returns None

    class _SkipPool:
        @asynccontextmanager
        async def acquire(self) -> Any:
            yield _SkipConn()

    count = await run_signal_sweep(_SkipPool())  # type: ignore[arg-type]
    assert count == 0


async def test_run_signal_sweep_counts_processed_market() -> None:
    """run_signal_sweep counts markets where signals were computed and written."""

    quote: dict[str, Any] = {
        "event_ts": _NOW,
        "bid": Decimal("0.48"),
        "ask": Decimal("0.52"),
        "bid_size": Decimal("100"),
        "ask_size": Decimal("100"),
    }

    call_n = 0

    class _Conn:
        async def fetch(self, query: str, *args: object) -> list[Any]:
            nonlocal call_n
            call_n += 1
            if call_n == 1:
                return [{"id": str(_MARKET_ID)}]  # open market ids
            return []  # book snapshot → depth_weighted_prob returns None

        async def fetchrow(self, query: str, *args: object) -> dict[str, Any] | None:
            return quote  # latest quote

        async def executemany(self, query: str, records: object) -> None:
            pass

    class _Pool:
        @asynccontextmanager
        async def acquire(self) -> Any:
            yield _Conn()

    count = await run_signal_sweep(_Pool())  # type: ignore[arg-type]
    assert count == 1
