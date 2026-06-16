"""Normalizer tests using real WS payloads captured from production."""

from __future__ import annotations

from decimal import Decimal

from meridian.events import BookDeltaEvent, BookEvent, EventKind, TradeEvent, Venue
from meridian.polymarket.normalize import (
    PolymarketBookState,
    normalize_polymarket_message,
    polymarket_market_id,
)


def test_market_id_is_deterministic() -> None:
    assert polymarket_market_id("123") == polymarket_market_id("123")
    assert polymarket_market_id("123") != polymarket_market_id("456")


def test_new_market_event_returns_empty() -> None:
    raw = {
        "id": "2561760",
        "question": "Will it rain?",
        "market": "0x4fa7...",
        "event_type": "new_market",
        "timestamp": "1781586812614",
    }
    assert normalize_polymarket_message(raw, book_state=PolymarketBookState()) == []


def test_tick_size_change_returns_empty() -> None:
    raw = {
        "event_type": "tick_size_change",
        "asset_id": "65818619657568813474341868652308942079804919287380422192892211131408793125422",
        "old_tick_size": "0.01",
        "new_tick_size": "0.001",
        "timestamp": "100000000",
    }
    assert normalize_polymarket_message(raw, book_state=PolymarketBookState()) == []


def test_unknown_event_type_returns_empty() -> None:
    raw = {"event_type": "something_new"}
    assert normalize_polymarket_message(raw, book_state=PolymarketBookState()) == []


def test_missing_event_type_returns_empty() -> None:
    assert normalize_polymarket_message({}, book_state=PolymarketBookState()) == []


def test_book_snapshot_normalizes() -> None:
    raw = {
        "event_type": "book",
        "asset_id": "98022490269692409998126496127597032490334070080325855126491859374983463996227",
        "market": "0x1fad72fae204143ff1c3035e99e7c0f65ea8d5cd9bd1070987bd1a3316f772be",
        "timestamp": "1781586812614",
        "hash": "ab469ae7a90965cf120f1bb2cbe15ffe7bc98c1c",
        "bids": [
            {"price": "0.01", "size": "2462.11"},
            {"price": "0.02", "size": "14.29"},
        ],
        "asks": [
            {"price": "0.52", "size": "25"},
            {"price": "0.50", "size": "60"},
        ],
    }
    events = normalize_polymarket_message(raw, book_state=PolymarketBookState())
    assert len(events) == 1
    ev = events[0]
    assert ev.venue is Venue.POLYMARKET
    assert ev.external_market_id == raw["asset_id"]
    assert ev.market_id == polymarket_market_id(raw["asset_id"])
    assert ev.payload.kind is EventKind.BOOK
    assert isinstance(ev.payload, BookEvent)

    bids = [lv for lv in ev.payload.levels if lv.side == "bid"]
    asks = [lv for lv in ev.payload.levels if lv.side == "ask"]
    # Best bid = highest price, level 0
    assert bids[0].price == Decimal("0.02") and bids[0].level == 0
    assert bids[1].price == Decimal("0.01") and bids[1].level == 1
    # Best ask = lowest price, level 0
    assert asks[0].price == Decimal("0.50") and asks[0].level == 0
    assert asks[1].price == Decimal("0.52") and asks[1].level == 1


def test_book_snapshot_resets_book_state() -> None:
    state = PolymarketBookState()
    asset_id = "ASSET-1"
    # Seed state with a level via a price_change, then a fresh book snapshot
    # should reset it so the next price_change at that level is "from zero".
    price_change = {
        "event_type": "price_change",
        "market": "0xmkt",
        "price_changes": [
            {"asset_id": asset_id, "price": "0.50", "size": "100", "side": "BUY"},
        ],
        "timestamp": "1000",
    }
    [ev1] = normalize_polymarket_message(price_change, book_state=state)
    assert isinstance(ev1.payload, BookDeltaEvent)
    assert ev1.payload.delta == Decimal("100")  # from 0 -> 100

    book = {
        "event_type": "book",
        "asset_id": asset_id,
        "bids": [],
        "asks": [],
        "timestamp": "2000",
    }
    normalize_polymarket_message(book, book_state=state)

    [ev2] = normalize_polymarket_message(price_change, book_state=state)
    assert isinstance(ev2.payload, BookDeltaEvent)
    assert ev2.payload.delta == Decimal("100")  # state was reset, so again from 0


def test_price_change_computes_signed_delta_from_absolute_size() -> None:
    state = PolymarketBookState()
    asset_a = "5904694069529905826680225678119990013628860098514322777023141327768111791078"
    asset_b = "59855952379545476461266875939089074481171378466995213858930892770855011813551"
    raw = {
        "event_type": "price_change",
        "market": "0xd86a816093fcd0a0e1ca440bc5ce199bd3c5a8d6139e044b076958164f8c5423",
        "price_changes": [
            {
                "asset_id": asset_a,
                "price": "0.39",
                "size": "100",
                "side": "BUY",
                "best_bid": "0.976",
                "best_ask": "0.977",
            },
            {
                "asset_id": asset_b,
                "price": "0.61",
                "size": "200",
                "side": "SELL",
            },
        ],
        "timestamp": "1757908892351",
    }
    events = normalize_polymarket_message(raw, book_state=state)
    assert len(events) == 2

    ev0 = events[0]
    assert isinstance(ev0.payload, BookDeltaEvent)
    assert ev0.payload.side == "bid"
    assert ev0.payload.price == Decimal("0.39")
    assert ev0.payload.delta == Decimal("100")  # first sighting: 0 -> 100

    ev1 = events[1]
    assert isinstance(ev1.payload, BookDeltaEvent)
    assert ev1.payload.side == "ask"
    assert ev1.payload.delta == Decimal("200")

    # sequence numbers must be distinct even though both share one timestamp
    assert ev0.sequence_no != ev1.sequence_no

    # A second update to the same level computes the delta vs. the cached size.
    raw2 = {
        "event_type": "price_change",
        "market": raw["market"],
        "price_changes": [
            {"asset_id": asset_a, "price": "0.39", "size": "70", "side": "BUY"},
        ],
        "timestamp": "1757908893000",
    }
    [ev2] = normalize_polymarket_message(raw2, book_state=state)
    assert isinstance(ev2.payload, BookDeltaEvent)
    assert ev2.payload.delta == Decimal("-30")  # 100 -> 70


def test_price_change_drops_zeroed_level_from_state() -> None:
    state = PolymarketBookState()
    asset_id = "ASSET-2"
    seed = {
        "event_type": "price_change",
        "market": "0xmkt",
        "price_changes": [{"asset_id": asset_id, "price": "0.50", "size": "10", "side": "BUY"}],
        "timestamp": "1",
    }
    normalize_polymarket_message(seed, book_state=state)

    zero_out = {
        "event_type": "price_change",
        "market": "0xmkt",
        "price_changes": [{"asset_id": asset_id, "price": "0.50", "size": "0", "side": "BUY"}],
        "timestamp": "2",
    }
    [ev] = normalize_polymarket_message(zero_out, book_state=state)
    assert isinstance(ev.payload, BookDeltaEvent)
    assert ev.payload.delta == Decimal("-10")

    # Level was dropped from state, so the next sighting is "from zero" again.
    [ev2] = normalize_polymarket_message(seed, book_state=state)
    assert isinstance(ev2.payload, BookDeltaEvent)
    assert ev2.payload.delta == Decimal("10")


def test_price_change_rejects_unknown_side() -> None:
    raw = {
        "event_type": "price_change",
        "market": "0xmkt",
        "price_changes": [
            {"asset_id": "X", "price": "0.5", "size": "1", "side": "WEIRD"},
        ],
        "timestamp": "1",
    }
    assert normalize_polymarket_message(raw, book_state=PolymarketBookState()) == []


def test_last_trade_price_normalizes_to_trade() -> None:
    asset_id = "114122071509644379678018727908709560226618148003371446110114509806601493071694"
    raw = {
        "asset_id": asset_id,
        "event_type": "last_trade_price",
        "fee_rate_bps": "0",
        "market": "0x6a67b9d828d53862160e470329ffea5246f338ecfffdf2cab45211ec578b0347",
        "price": "0.456",
        "side": "BUY",
        "size": "219.217767",
        "timestamp": "1750428146322",
    }
    [ev] = normalize_polymarket_message(raw, book_state=PolymarketBookState())
    assert ev.venue is Venue.POLYMARKET
    assert ev.external_market_id == raw["asset_id"]
    assert ev.payload.kind is EventKind.TRADE
    assert isinstance(ev.payload, TradeEvent)
    assert ev.payload.price == Decimal("0.456")
    assert ev.payload.size == Decimal("219.217767")
    assert ev.payload.aggressor == "buy"
