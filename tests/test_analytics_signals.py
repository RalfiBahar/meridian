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
    compute_market_signals,
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
