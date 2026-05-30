"""Normalize raw Kalshi WS messages into CanonicalEvent.

The normalizer is the only place that knows Kalshi's wire format. Everything
downstream (persistence, analytics, calibration, arb engine) reads
`CanonicalEvent` exclusively, so adding Polymarket (Phase 1d) is "write
another normalizer."

The function returns `None` for control messages (`subscribed`, `ok`, ...)
and unknown types, so callers can `for raw in stream: if ev := normalize(...):`
without branching on type.

Market IDs are derived deterministically from the Kalshi ticker via UUIDv5
under a fixed namespace, so the same ticker always maps to the same UUID
across processes and restarts. Phase 1c.2 will replace this with a real
`markets` table lookup; until then, this keeps the canonical event shape
uniform without requiring a DB roundtrip.
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
    QuoteEvent,
    StatusEvent,
    Venue,
)

# Fixed namespace for deterministic Kalshi market IDs.
# This value never changes — changing it would invalidate all derived UUIDs.
KALSHI_UUID_NAMESPACE = UUID("4d2c9b7a-1f3e-4d18-9c4a-7e8f3a2b5c1d")


def kalshi_market_id(ticker: str) -> UUID:
    """Deterministic UUID for a Kalshi market, derived from its ticker."""
    return uuid5(KALSHI_UUID_NAMESPACE, ticker)


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _parse_event_ts(msg: dict[str, Any]) -> datetime:
    """Extract the venue's event timestamp; fall back to wall clock if absent."""
    ts_ms = msg.get("ts_ms")
    if isinstance(ts_ms, int):
        return datetime.fromtimestamp(ts_ms / 1000, tz=UTC)
    ts = msg.get("ts") or msg.get("time")
    if isinstance(ts, str):
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return _now()


def _sequence_no(raw: dict[str, Any], msg: dict[str, Any]) -> int:
    """Pick a monotonic-per-stream identifier.

    Orderbook channels include `seq` in the envelope. The `ticker` channel
    doesn't, so we fall back to `ts_ms` (still monotonic per market).
    """
    seq = raw.get("seq")
    if isinstance(seq, int):
        return seq
    ts_ms = msg.get("ts_ms")
    if isinstance(ts_ms, int):
        return ts_ms
    return 0


def _sorted_levels(rows: list[Any], side: BookSide) -> list[BookLevel]:
    """Sort raw `[price_str, size_str]` rows descending by price (best first)."""
    parsed: list[tuple[Decimal, Decimal]] = []
    for row in rows:
        if not isinstance(row, list | tuple) or len(row) != 2:
            continue
        parsed.append((Decimal(str(row[0])), Decimal(str(row[1]))))
    parsed.sort(key=lambda pair: pair[0], reverse=True)
    return [
        BookLevel(side=side, level=i, price=price, size=size)
        for i, (price, size) in enumerate(parsed)
    ]


def _payload_for_snapshot(msg: dict[str, Any]) -> EventPayload | None:
    yes_rows = msg.get("yes_dollars_fp") or msg.get("yes_dollars") or []
    no_rows = msg.get("no_dollars_fp") or msg.get("no_dollars") or []
    levels = _sorted_levels(yes_rows, "yes") + _sorted_levels(no_rows, "no")
    return BookEvent(kind=EventKind.BOOK, levels=levels)


def _payload_for_delta(msg: dict[str, Any]) -> EventPayload | None:
    side = msg.get("side")
    if side not in ("yes", "no"):
        return None
    price = msg.get("price_dollars")
    delta = msg.get("delta_fp")
    if price is None or delta is None:
        return None
    return BookDeltaEvent(
        kind=EventKind.BOOK_DELTA,
        side=side,
        price=Decimal(str(price)),
        delta=Decimal(str(delta)),
    )


def _payload_for_ticker(msg: dict[str, Any]) -> EventPayload | None:
    """Map the composite `ticker` channel to a top-of-book QuoteEvent.

    We discard the last-trade fields here; the dedicated `trade` channel
    carries those.
    """
    bid = msg.get("yes_bid_dollars")
    ask = msg.get("yes_ask_dollars")
    bid_size = msg.get("yes_bid_size_fp")
    ask_size = msg.get("yes_ask_size_fp")
    return QuoteEvent(
        kind=EventKind.QUOTE,
        bid=Decimal(str(bid)) if bid is not None else None,
        ask=Decimal(str(ask)) if ask is not None else None,
        bid_size=Decimal(str(bid_size)) if bid_size is not None else None,
        ask_size=Decimal(str(ask_size)) if ask_size is not None else None,
    )


_LIFECYCLE_STATUSES = {
    "open": "open",
    "active": "open",
    "halted": "halted",
    "paused": "halted",
    "closed": "closed",
    "settled": "settled",
    "finalized": "settled",
}


def _payload_for_lifecycle(msg: dict[str, Any]) -> EventPayload | None:
    raw_status = (msg.get("status") or msg.get("state") or "").lower()
    status = _LIFECYCLE_STATUSES.get(raw_status)
    if status is None:
        return None
    return StatusEvent(kind=EventKind.STATUS, status=status)  # type: ignore[arg-type]


# Routing table: message type -> payload builder. None entries are control
# messages (acks, errors) that the caller should treat as informational.
_PAYLOAD_BUILDERS: dict[str, Any] = {
    "orderbook_snapshot": _payload_for_snapshot,
    "orderbook_delta": _payload_for_delta,
    "ticker": _payload_for_ticker,
    "market_lifecycle_v2": _payload_for_lifecycle,
    "market_lifecycle": _payload_for_lifecycle,
}


def normalize_kalshi_message(raw: dict[str, Any]) -> CanonicalEvent | None:
    """Convert one raw Kalshi WS message into a CanonicalEvent.

    Returns `None` for control messages (`subscribed`, `ok`, `error`), for
    payloads that lack a recognizable shape, and for message types we
    don't yet model.
    """
    msg_type = raw.get("type")
    if not isinstance(msg_type, str):
        return None
    if msg_type in ("subscribed", "ok", "error", "unsubscribed"):
        return None
    builder = _PAYLOAD_BUILDERS.get(msg_type)
    if builder is None:
        return None
    msg = raw.get("msg") or {}
    if not isinstance(msg, dict):
        return None
    ticker = msg.get("market_ticker")
    if not isinstance(ticker, str) or not ticker:
        return None
    payload = builder(msg)
    if payload is None:
        return None
    return CanonicalEvent(
        venue=Venue.KALSHI,
        external_market_id=ticker,
        market_id=kalshi_market_id(ticker),
        sequence_no=_sequence_no(raw, msg),
        event_ts=_parse_event_ts(msg),
        ingest_ts=_now(),
        payload=payload,
    )
