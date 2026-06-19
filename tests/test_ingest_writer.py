"""Unit tests for TickWriter — all paths, no real DB."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest

from meridian.events import (
    BookDeltaEvent,
    BookEvent,
    BookLevel,
    CanonicalEvent,
    EventKind,
    QuoteEvent,
    StatusEvent,
    TradeEvent,
    Venue,
)
from meridian.ingest.writer import TickWriter

_MARKET_ID = UUID("00000000-0000-0000-0000-000000000001")
_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _make_event(payload: Any) -> CanonicalEvent:
    return CanonicalEvent(
        venue=Venue.KALSHI,
        external_market_id="TEST-TICKER",
        market_id=_MARKET_ID,
        sequence_no=42,
        event_ts=_NOW,
        ingest_ts=_NOW,
        payload=payload,
    )


# ---------------------------------------------------------------------------
# QuoteEvent
# ---------------------------------------------------------------------------


async def test_write_quote_returns_one_on_insert(mock_pool: Any) -> None:
    event = _make_event(
        QuoteEvent(
            kind=EventKind.QUOTE,
            bid=Decimal("0.50"),
            ask=Decimal("0.52"),
            bid_size=Decimal("10"),
            ask_size=Decimal("5"),
        )
    )
    writer = TickWriter(mock_pool)
    result = await writer.write(event)
    assert result == 1


async def test_write_quote_conflict_returns_zero(mock_pool: Any, mock_conn: Any) -> None:
    mock_conn.execute_result = "INSERT 0 0"
    event = _make_event(QuoteEvent(kind=EventKind.QUOTE, bid=Decimal("0.48"), ask=Decimal("0.52")))
    writer = TickWriter(mock_pool)
    result = await writer.write(event)
    assert result == 0


async def test_write_quote_null_sizes(mock_pool: Any, mock_conn: Any) -> None:
    event = _make_event(QuoteEvent(kind=EventKind.QUOTE))
    writer = TickWriter(mock_pool)
    result = await writer.write(event)
    assert result == 1
    query, args = mock_conn.executions[0]
    assert "INSERT INTO ticks" in query
    assert args[3] == "quote"


# ---------------------------------------------------------------------------
# TradeEvent
# ---------------------------------------------------------------------------


async def test_write_trade_returns_one(mock_pool: Any) -> None:
    event = _make_event(
        TradeEvent(
            kind=EventKind.TRADE,
            price=Decimal("0.50"),
            size=Decimal("100"),
            aggressor="buy",
        )
    )
    writer = TickWriter(mock_pool)
    result = await writer.write(event)
    assert result == 1


async def test_write_trade_passes_kind_and_aggressor(mock_pool: Any, mock_conn: Any) -> None:
    event = _make_event(
        TradeEvent(
            kind=EventKind.TRADE, price=Decimal("0.60"), size=Decimal("50"), aggressor="sell"
        )
    )
    writer = TickWriter(mock_pool)
    await writer.write(event)
    _, args = mock_conn.executions[0]
    assert args[3] == "trade"
    assert args[10] == "sell"


async def test_write_trade_null_aggressor(mock_pool: Any, mock_conn: Any) -> None:
    event = _make_event(TradeEvent(kind=EventKind.TRADE, price=Decimal("0.50"), size=Decimal("10")))
    writer = TickWriter(mock_pool)
    await writer.write(event)
    _, args = mock_conn.executions[0]
    assert args[10] is None


# ---------------------------------------------------------------------------
# BookEvent
# ---------------------------------------------------------------------------


async def test_write_book_event_empty_levels_returns_zero(mock_pool: Any) -> None:
    event = _make_event(BookEvent(kind=EventKind.BOOK, levels=[]))
    writer = TickWriter(mock_pool)
    result = await writer.write(event)
    assert result == 0


async def test_write_book_event_empty_levels_no_db_call(mock_pool: Any, mock_conn: Any) -> None:
    event = _make_event(BookEvent(kind=EventKind.BOOK, levels=[]))
    writer = TickWriter(mock_pool)
    await writer.write(event)
    assert not mock_conn.executemany_calls


async def test_write_book_event_returns_level_count(mock_pool: Any) -> None:
    levels = [
        BookLevel(side="bid", level=0, price=Decimal("0.50"), size=Decimal("100")),
        BookLevel(side="ask", level=0, price=Decimal("0.52"), size=Decimal("50")),
        BookLevel(side="bid", level=1, price=Decimal("0.48"), size=Decimal("200")),
    ]
    event = _make_event(BookEvent(kind=EventKind.BOOK, levels=levels))
    writer = TickWriter(mock_pool)
    result = await writer.write(event)
    assert result == 3


async def test_write_book_event_uses_executemany(mock_pool: Any, mock_conn: Any) -> None:
    levels = [
        BookLevel(side="yes", level=0, price=Decimal("0.50"), size=Decimal("10")),
        BookLevel(side="no", level=0, price=Decimal("0.50"), size=Decimal("10")),
    ]
    event = _make_event(BookEvent(kind=EventKind.BOOK, levels=levels))
    writer = TickWriter(mock_pool)
    await writer.write(event)
    assert len(mock_conn.executemany_calls) == 1
    query, records = mock_conn.executemany_calls[0]
    assert "INSERT INTO book_snapshots" in query
    assert len(records) == 2


# ---------------------------------------------------------------------------
# BookDeltaEvent
# ---------------------------------------------------------------------------


async def test_write_book_delta_returns_one(mock_pool: Any) -> None:
    event = _make_event(
        BookDeltaEvent(
            kind=EventKind.BOOK_DELTA,
            side="bid",
            price=Decimal("0.50"),
            delta=Decimal("15"),
        )
    )
    writer = TickWriter(mock_pool)
    result = await writer.write(event)
    assert result == 1


async def test_write_book_delta_payload_json(mock_pool: Any, mock_conn: Any) -> None:
    event = _make_event(
        BookDeltaEvent(
            kind=EventKind.BOOK_DELTA,
            side="ask",
            price=Decimal("0.52"),
            delta=Decimal("-5"),
        )
    )
    writer = TickWriter(mock_pool)
    await writer.write(event)
    _, args = mock_conn.executions[0]
    assert args[3] == "book_delta"
    payload = json.loads(args[11])
    assert payload["side"] == "ask"
    assert payload["price"] == "0.52"
    assert payload["delta"] == "-5"


# ---------------------------------------------------------------------------
# StatusEvent
# ---------------------------------------------------------------------------


async def test_write_status_returns_one(mock_pool: Any) -> None:
    event = _make_event(StatusEvent(kind=EventKind.STATUS, status="open"))
    writer = TickWriter(mock_pool)
    result = await writer.write(event)
    assert result == 1


async def test_write_status_payload_json(mock_pool: Any, mock_conn: Any) -> None:
    for status in ("open", "halted", "closed", "settled"):
        mock_conn.executions.clear()
        event = _make_event(StatusEvent(kind=EventKind.STATUS, status=status))  # type: ignore[arg-type]
        writer = TickWriter(mock_pool)
        await writer.write(event)
        _, args = mock_conn.executions[0]
        assert args[3] == "status"
        payload = json.loads(args[11])
        assert payload["status"] == status


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


async def test_write_dispatches_all_payload_types(mock_pool: Any) -> None:
    writer = TickWriter(mock_pool)
    payloads: list[Any] = [
        QuoteEvent(kind=EventKind.QUOTE),
        TradeEvent(kind=EventKind.TRADE, price=Decimal("0.5"), size=Decimal("1")),
        BookEvent(kind=EventKind.BOOK, levels=[]),
        BookDeltaEvent(
            kind=EventKind.BOOK_DELTA, side="bid", price=Decimal("0.5"), delta=Decimal("1")
        ),
        StatusEvent(kind=EventKind.STATUS, status="open"),
    ]
    for payload in payloads:
        event = _make_event(payload)
        # Should not raise
        await writer.write(event)


# ---------------------------------------------------------------------------
# Idempotency: ON CONFLICT returns exact row-count
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pg_result", "expected"),
    [
        ("INSERT 0 1", 1),
        ("INSERT 0 0", 0),
    ],
)
async def test_insert_tick_returns_correct_count(
    mock_pool: Any, mock_conn: Any, pg_result: str, expected: int
) -> None:
    mock_conn.execute_result = pg_result
    event = _make_event(StatusEvent(kind=EventKind.STATUS, status="open"))
    writer = TickWriter(mock_pool)
    result = await writer.write(event)
    assert result == expected
