"""Canonical event model: discriminator dispatch, validation, immutability."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from meridian.events import (
    BookDeltaEvent,
    BookEvent,
    BookLevel,
    CanonicalEvent,
    EventKind,
    EventPayload,
    QuoteEvent,
    StatusEvent,
    TradeEvent,
    Venue,
)


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _ev(
    payload: QuoteEvent | TradeEvent | BookEvent | BookDeltaEvent | StatusEvent,
) -> CanonicalEvent:
    return CanonicalEvent(
        venue=Venue.KALSHI,
        external_market_id="KX-TEST-1",
        market_id=uuid4(),
        sequence_no=1,
        event_ts=_now(),
        ingest_ts=_now(),
        payload=payload,
    )


def test_quote_event_constructs() -> None:
    q = QuoteEvent(
        kind=EventKind.QUOTE,
        bid=Decimal("0.6200"),
        ask=Decimal("0.6600"),
        bid_size=Decimal("800"),
        ask_size=Decimal("500"),
    )
    assert q.bid == Decimal("0.6200")


def test_trade_event_constructs() -> None:
    t = TradeEvent(
        kind=EventKind.TRADE,
        price=Decimal("0.6500"),
        size=Decimal("100"),
        aggressor="buy",
    )
    assert t.size == Decimal("100")


def test_book_event_constructs_with_levels() -> None:
    b = BookEvent(
        kind=EventKind.BOOK,
        levels=[
            BookLevel(side="bid", level=0, price=Decimal("0.62"), size=Decimal("800")),
            BookLevel(side="ask", level=0, price=Decimal("0.66"), size=Decimal("500")),
        ],
    )
    assert len(b.levels) == 2


def test_book_event_accepts_kalshi_sides() -> None:
    b = BookEvent(
        kind=EventKind.BOOK,
        levels=[
            BookLevel(side="yes", level=0, price=Decimal("0.02"), size=Decimal("3192.31")),
            BookLevel(side="no", level=0, price=Decimal("0.03"), size=Decimal("604.16")),
        ],
    )
    assert b.levels[0].side == "yes"
    assert b.levels[1].size == Decimal("604.16")


def test_book_delta_event_constructs() -> None:
    d = BookDeltaEvent(
        kind=EventKind.BOOK_DELTA,
        side="no",
        price=Decimal("0.63"),
        delta=Decimal("-13"),
    )
    assert d.delta == Decimal("-13")


def test_status_event_constructs() -> None:
    s = StatusEvent(kind=EventKind.STATUS, status="open")
    assert s.status == "open"


def test_canonical_event_wraps_payload() -> None:
    ev = _ev(StatusEvent(kind=EventKind.STATUS, status="halted"))
    assert ev.venue == Venue.KALSHI
    assert ev.payload.kind == EventKind.STATUS


def test_discriminator_dispatches_from_dict() -> None:
    adapter: TypeAdapter[EventPayload] = TypeAdapter(EventPayload)
    parsed = adapter.validate_python({"kind": "trade", "price": "0.65", "size": "100"})
    assert isinstance(parsed, TradeEvent)
    assert parsed.price == Decimal("0.65")


def test_discriminator_dispatches_book_delta() -> None:
    adapter: TypeAdapter[EventPayload] = TypeAdapter(EventPayload)
    parsed = adapter.validate_python(
        {"kind": "book_delta", "side": "yes", "price": "0.5", "delta": "10"}
    )
    assert isinstance(parsed, BookDeltaEvent)
    assert parsed.delta == Decimal("10")


def test_discriminator_missing_kind_rejected() -> None:
    adapter: TypeAdapter[EventPayload] = TypeAdapter(EventPayload)
    with pytest.raises(ValidationError):
        adapter.validate_python({"price": "0.65", "size": "100"})


def test_price_out_of_range_rejected() -> None:
    with pytest.raises(ValidationError):
        QuoteEvent(kind=EventKind.QUOTE, bid=Decimal("1.5"))
    with pytest.raises(ValidationError):
        TradeEvent(kind=EventKind.TRADE, price=Decimal("-0.01"), size=Decimal("1"))


def test_event_is_frozen() -> None:
    ev = _ev(StatusEvent(kind=EventKind.STATUS, status="open"))
    with pytest.raises(ValidationError):
        ev.sequence_no = 99


def test_naive_datetime_rejected() -> None:
    with pytest.raises(ValidationError):
        CanonicalEvent(
            venue=Venue.KALSHI,
            external_market_id="KX-TEST-1",
            market_id=uuid4(),
            sequence_no=1,
            event_ts=datetime(2026, 1, 1),  # naive
            ingest_ts=_now(),
            payload=StatusEvent(kind=EventKind.STATUS, status="open"),
        )


def test_unknown_field_rejected() -> None:
    with pytest.raises(ValidationError):
        QuoteEvent.model_validate({"kind": "quote", "bid": "0.5", "junk_field": "x"})
