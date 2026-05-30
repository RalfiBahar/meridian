"""Normalizer tests using real WS payloads captured from production."""

from __future__ import annotations

from decimal import Decimal

from meridian.events import (
    BookDeltaEvent,
    BookEvent,
    EventKind,
    QuoteEvent,
    StatusEvent,
    Venue,
)
from meridian.kalshi.normalize import kalshi_market_id, normalize_kalshi_message


def test_subscribed_ack_returns_none() -> None:
    raw = {"type": "subscribed", "id": 1, "msg": {"channel": "orderbook_delta", "sid": 1}}
    assert normalize_kalshi_message(raw) is None


def test_unknown_type_returns_none() -> None:
    assert normalize_kalshi_message({"type": "something_new", "msg": {}}) is None


def test_empty_message_returns_none() -> None:
    assert normalize_kalshi_message({}) is None
    assert normalize_kalshi_message({"type": "ticker"}) is None  # no msg
    assert normalize_kalshi_message({"type": "ticker", "msg": {}}) is None  # no ticker


def test_orderbook_snapshot_normalizes() -> None:
    raw = {
        "type": "orderbook_snapshot",
        "sid": 1,
        "seq": 1,
        "msg": {
            "market_ticker": "KXFED-26JUN-T3.75",
            "market_id": "2906b4ee-0cba-4152-aa0f-1f6161908f47",
            "yes_dollars_fp": [
                ["0.0100", "289061.03"],
                ["0.0200", "3192.31"],
            ],
            "no_dollars_fp": [
                ["0.0100", "25921.23"],
                ["0.0200", "21268.49"],
                ["0.0300", "604.16"],
            ],
        },
    }
    ev = normalize_kalshi_message(raw)
    assert ev is not None
    assert ev.venue is Venue.KALSHI
    assert ev.external_market_id == "KXFED-26JUN-T3.75"
    assert ev.market_id == kalshi_market_id("KXFED-26JUN-T3.75")
    assert ev.sequence_no == 1
    assert ev.payload.kind is EventKind.BOOK
    assert isinstance(ev.payload, BookEvent)
    # Best YES is highest yes-side price (0.0200), level 0
    yes = [lv for lv in ev.payload.levels if lv.side == "yes"]
    assert yes[0].price == Decimal("0.0200") and yes[0].level == 0
    assert yes[1].price == Decimal("0.0100") and yes[1].level == 1
    # Best NO is highest no-side price (0.0300)
    no = [lv for lv in ev.payload.levels if lv.side == "no"]
    assert no[0].price == Decimal("0.0300") and no[0].level == 0
    assert no[0].size == Decimal("604.16")


def test_orderbook_delta_normalizes_signed() -> None:
    raw = {
        "type": "orderbook_delta",
        "sid": 1,
        "seq": 63,
        "msg": {
            "market_ticker": "KXFED-27JAN-T4.00",
            "market_id": "b635b4d7-7a60-4a4e-beba-c389360b00dd",
            "price_dollars": "0.6300",
            "delta_fp": "-13.00",
            "side": "no",
            "ts": "2026-05-28T05:35:09.623423Z",
            "ts_ms": 1779946509623,
        },
    }
    ev = normalize_kalshi_message(raw)
    assert ev is not None
    assert ev.sequence_no == 63
    assert ev.event_ts.year == 2026 and ev.event_ts.month == 5
    assert ev.payload.kind is EventKind.BOOK_DELTA
    assert isinstance(ev.payload, BookDeltaEvent)
    assert ev.payload.side == "no"
    assert ev.payload.price == Decimal("0.6300")
    assert ev.payload.delta == Decimal("-13.00")


def test_orderbook_delta_rejects_unknown_side() -> None:
    raw = {
        "type": "orderbook_delta",
        "seq": 1,
        "msg": {
            "market_ticker": "X",
            "side": "weird",
            "price_dollars": "0.5",
            "delta_fp": "1",
        },
    }
    assert normalize_kalshi_message(raw) is None


def test_ticker_normalizes_to_quote() -> None:
    raw = {
        "type": "ticker",
        "sid": 2,
        "msg": {
            "market_id": "b635b4d7-7a60-4a4e-beba-c389360b00dd",
            "market_ticker": "KXFED-27JAN-T4.00",
            "price_dollars": "0.3400",
            "yes_bid_dollars": "0.3100",
            "yes_ask_dollars": "0.3700",
            "yes_bid_size_fp": "11.00",
            "yes_ask_size_fp": "16.00",
            "last_trade_size_fp": "1.00",
            "volume_fp": "6495.96",
            "ts_ms": 1779946509637,
        },
    }
    ev = normalize_kalshi_message(raw)
    assert ev is not None
    assert ev.sequence_no == 1779946509637  # falls back to ts_ms
    assert ev.payload.kind is EventKind.QUOTE
    assert isinstance(ev.payload, QuoteEvent)
    assert ev.payload.bid == Decimal("0.3100")
    assert ev.payload.ask == Decimal("0.3700")
    assert ev.payload.bid_size == Decimal("11.00")
    assert ev.payload.ask_size == Decimal("16.00")


def test_market_lifecycle_open_normalizes() -> None:
    raw = {
        "type": "market_lifecycle_v2",
        "msg": {
            "market_ticker": "KXFED-26JUN-T3.75",
            "status": "open",
            "ts_ms": 1779900000000,
        },
    }
    ev = normalize_kalshi_message(raw)
    assert ev is not None
    assert ev.payload.kind is EventKind.STATUS
    assert isinstance(ev.payload, StatusEvent)
    assert ev.payload.status == "open"


def test_market_lifecycle_active_maps_to_open() -> None:
    raw = {
        "type": "market_lifecycle_v2",
        "msg": {"market_ticker": "X", "status": "active", "ts_ms": 1},
    }
    ev = normalize_kalshi_message(raw)
    assert ev is not None
    assert isinstance(ev.payload, StatusEvent)
    assert ev.payload.status == "open"


def test_market_id_is_deterministic() -> None:
    assert kalshi_market_id("KXFED-26JUN-T3.75") == kalshi_market_id("KXFED-26JUN-T3.75")
    assert kalshi_market_id("KXFED-26JUN-T3.75") != kalshi_market_id("KXFED-26JUN-T3.50")
