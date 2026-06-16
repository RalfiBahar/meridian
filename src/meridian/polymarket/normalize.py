"""Normalize raw Polymarket WS messages into CanonicalEvent.

Mirrors `kalshi/normalize.py`'s role — the only place that knows
Polymarket's wire format — with two structural differences forced by the
protocol itself (see `docs/polymarket.md` and ADR-015/ADR-016):

1. `normalize_polymarket_message()` returns a **list** of events, not one:
   a single `price_change` message can batch updates for several
   `(asset_id, side, price)` levels.
2. It takes a `book_state: PolymarketBookState` argument. Polymarket's
   `price_change` reports the *absolute* resting size at a level, not a
   signed delta like Kalshi's `delta_fp`. `PolymarketBookState` is the
   minimal piece of caller-owned state needed to turn "new absolute size"
   into the signed `BookDeltaEvent.delta` our canonical schema expects,
   without making the normalizer itself stateful.

Market identity is keyed on `token_id` (the WS `asset_id` / REST
`token_id`), not the parent `condition_id` — each token has its own order
book. See ADR-015.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid5

from meridian.events import (
    BookDeltaEvent,
    BookEvent,
    BookLevel,
    BookSide,
    CanonicalEvent,
    EventKind,
    EventPayload,
    TradeEvent,
    Venue,
)

# Fixed namespace for deterministic Polymarket market IDs, distinct from
# Kalshi's. This value never changes — changing it would invalidate all
# derived UUIDs.
POLYMARKET_UUID_NAMESPACE = UUID("8a1c4e6f-2b9d-4a7e-91c5-3f6d8b0a2e7c")

_SIDE_MAP: dict[str, BookSide] = {"BUY": "bid", "SELL": "ask"}
_AGGRESSOR_MAP: dict[str, str] = {"BUY": "buy", "SELL": "sell"}

# Sub-index multiplier so a batched message's entries get distinct
# monotonic-ish sequence numbers derived from one wall-clock timestamp.
# Polymarket publishes no real sequence number at all (see docs/polymarket.md
# "No gap detection").
_BATCH_SHIFT = 1000


def polymarket_market_id(token_id: str) -> UUID:
    """Deterministic UUID for a Polymarket token, derived from its token_id."""
    return uuid5(POLYMARKET_UUID_NAMESPACE, token_id)


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _parse_event_ts(raw: dict[str, Any]) -> datetime:
    timestamp = raw.get("timestamp")
    if timestamp is not None:
        try:
            return datetime.fromtimestamp(int(timestamp) / 1000, tz=UTC)
        except (TypeError, ValueError):
            pass
    return _now()


def _sequence_no(raw: dict[str, Any], index: int = 0) -> int:
    timestamp = raw.get("timestamp")
    if timestamp is None:
        return index
    try:
        ts_ms = int(timestamp)
    except (TypeError, ValueError):
        return index
    return ts_ms * _BATCH_SHIFT + index


class PolymarketBookState:
    """Per-`(asset_id, side, price)` resting-size cache.

    `apply()` records the new absolute size and returns the signed delta
    versus what was previously known (treating an unseen level as size 0).
    `reset()` drops all cached levels for one asset — called whenever a
    fresh `book` snapshot arrives for that asset, so a missed message can't
    cause unbounded drift (see "No gap detection" in docs/polymarket.md).
    """

    def __init__(self) -> None:
        self._sizes: dict[tuple[str, BookSide, Decimal], Decimal] = {}

    def apply(self, asset_id: str, side: BookSide, price: Decimal, new_size: Decimal) -> Decimal:
        key = (asset_id, side, price)
        previous = self._sizes.get(key, Decimal(0))
        if new_size <= 0:
            self._sizes.pop(key, None)
        else:
            self._sizes[key] = new_size
        return new_size - previous

    def reset(self, asset_id: str) -> None:
        for key in [k for k in self._sizes if k[0] == asset_id]:
            del self._sizes[key]


def _levels_for_book(asset_id: str, rows: list[Any], side: BookSide) -> list[BookLevel]:
    parsed: list[tuple[Decimal, Decimal]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        price, size = row.get("price"), row.get("size")
        if price is None or size is None:
            continue
        parsed.append((Decimal(str(price)), Decimal(str(size))))
    reverse = side == "bid"  # bids best-first descending; asks best-first ascending
    parsed.sort(key=lambda pair: pair[0], reverse=reverse)
    return [
        BookLevel(side=side, level=i, price=price, size=size)
        for i, (price, size) in enumerate(parsed)
    ]


def _normalize_book(raw: dict[str, Any], book_state: PolymarketBookState) -> list[CanonicalEvent]:
    asset_id = raw.get("asset_id")
    if not isinstance(asset_id, str) or not asset_id:
        return []
    book_state.reset(asset_id)
    bids = raw.get("bids") or []
    asks = raw.get("asks") or []
    levels = _levels_for_book(asset_id, bids, "bid") + _levels_for_book(asset_id, asks, "ask")
    payload: EventPayload = BookEvent(kind=EventKind.BOOK, levels=levels)
    return [
        CanonicalEvent(
            venue=Venue.POLYMARKET,
            external_market_id=asset_id,
            market_id=polymarket_market_id(asset_id),
            sequence_no=_sequence_no(raw),
            event_ts=_parse_event_ts(raw),
            ingest_ts=_now(),
            payload=payload,
        )
    ]


def _normalize_price_change(
    raw: dict[str, Any], book_state: PolymarketBookState
) -> list[CanonicalEvent]:
    changes = raw.get("price_changes")
    if not isinstance(changes, list):
        return []
    event_ts = _parse_event_ts(raw)
    events: list[CanonicalEvent] = []
    for i, change in enumerate(changes):
        if not isinstance(change, dict):
            continue
        asset_id = change.get("asset_id")
        side = _SIDE_MAP.get(str(change.get("side")))
        price = change.get("price")
        size = change.get("size")
        if not isinstance(asset_id, str) or side is None or price is None or size is None:
            continue
        delta = book_state.apply(asset_id, side, Decimal(str(price)), Decimal(str(size)))
        payload: EventPayload = BookDeltaEvent(
            kind=EventKind.BOOK_DELTA,
            side=side,
            price=Decimal(str(price)),
            delta=delta,
        )
        events.append(
            CanonicalEvent(
                venue=Venue.POLYMARKET,
                external_market_id=asset_id,
                market_id=polymarket_market_id(asset_id),
                sequence_no=_sequence_no(raw, i),
                event_ts=event_ts,
                ingest_ts=_now(),
                payload=payload,
            )
        )
    return events


def _normalize_last_trade_price(raw: dict[str, Any]) -> list[CanonicalEvent]:
    asset_id = raw.get("asset_id")
    price = raw.get("price")
    size = raw.get("size")
    if not isinstance(asset_id, str) or price is None or size is None:
        return []
    aggressor = _AGGRESSOR_MAP.get(str(raw.get("side")))
    payload: EventPayload = TradeEvent(
        kind=EventKind.TRADE,
        price=Decimal(str(price)),
        size=Decimal(str(size)),
        aggressor=aggressor,  # type: ignore[arg-type]
    )
    return [
        CanonicalEvent(
            venue=Venue.POLYMARKET,
            external_market_id=asset_id,
            market_id=polymarket_market_id(asset_id),
            sequence_no=_sequence_no(raw),
            event_ts=_parse_event_ts(raw),
            ingest_ts=_now(),
            payload=payload,
        )
    ]


# `best_bid_ask` is intentionally absent: it duplicates `book`/`price_change`
# data and is only emitted with `custom_feature_enabled=True`, which we
# don't request by default (see ws.py). `new_market`, `market_resolved`,
# and `tick_size_change` are informational; we don't yet model them as
# CanonicalEvents.
_NO_OP_TYPES = frozenset({"new_market", "market_resolved", "tick_size_change", "best_bid_ask"})


def normalize_polymarket_message(
    raw: dict[str, Any],
    *,
    book_state: PolymarketBookState,
) -> list[CanonicalEvent]:
    """Convert one raw Polymarket WS message into zero or more CanonicalEvents.

    Returns `[]` for informational message types (see `_NO_OP_TYPES`) and
    for payloads that lack a recognizable shape.
    """
    event_type = raw.get("event_type")
    if not isinstance(event_type, str) or event_type in _NO_OP_TYPES:
        return []
    if event_type == "book":
        return _normalize_book(raw, book_state)
    if event_type == "price_change":
        return _normalize_price_change(raw, book_state)
    if event_type == "last_trade_price":
        return _normalize_last_trade_price(raw)
    return []
