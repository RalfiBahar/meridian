"""Canonical cross-venue event models.

Every venue-specific parser normalizes its raw messages into a CanonicalEvent
before any downstream consumer touches them. This keeps the rest of the
system venue-agnostic.

Sizes are `Decimal` (not `int`) to accommodate venues that support fractional
trading — Kalshi reports sizes like `"19.00"` in its `*_fp` (floating-point)
fields. Prices are constrained to [0, 1] via `PriceDecimal`.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class Venue(StrEnum):
    KALSHI = "kalshi"
    POLYMARKET = "polymarket"


class EventKind(StrEnum):
    QUOTE = "quote"
    TRADE = "trade"
    BOOK = "book"
    BOOK_DELTA = "book_delta"
    STATUS = "status"


PriceDecimal = Annotated[Decimal, Field(ge=Decimal(0), le=Decimal(1))]
SizeDecimal = Annotated[Decimal, Field(ge=Decimal(0))]
# A "side" on the order book. `bid`/`ask` are the universal microstructure
# terms; `yes`/`no` are Kalshi's native convention (each side is quoted as a
# bid on the corresponding YES or NO contract). We preserve raw venue
# semantics here and let analytics canonicalize.
BookSide = Literal["bid", "ask", "yes", "no"]


class _Payload(BaseModel):
    """Common config for payload variants: frozen + reject unknown fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class QuoteEvent(_Payload):
    kind: Literal[EventKind.QUOTE]
    bid: PriceDecimal | None = None
    ask: PriceDecimal | None = None
    bid_size: SizeDecimal | None = None
    ask_size: SizeDecimal | None = None


class TradeEvent(_Payload):
    kind: Literal[EventKind.TRADE]
    price: PriceDecimal
    size: Annotated[Decimal, Field(gt=Decimal(0))]
    aggressor: Literal["buy", "sell"] | None = None


class BookLevel(BaseModel):
    model_config = ConfigDict(frozen=True)
    side: BookSide
    level: int = Field(ge=0)
    price: PriceDecimal
    size: SizeDecimal


class BookEvent(_Payload):
    """Full L2 book snapshot. `levels` includes both sides, ordered best-first per side."""

    kind: Literal[EventKind.BOOK]
    levels: list[BookLevel]


class BookDeltaEvent(_Payload):
    """One signed level update from a streaming book channel.

    `delta` is signed: negative shrinks the level, positive grows it. If the
    resulting size at `(side, price)` is <= 0, the consumer drops the level.
    """

    kind: Literal[EventKind.BOOK_DELTA]
    side: BookSide
    price: PriceDecimal
    delta: Decimal


class StatusEvent(_Payload):
    kind: Literal[EventKind.STATUS]
    status: Literal["open", "halted", "closed", "settled"]


EventPayload = Annotated[
    QuoteEvent | TradeEvent | BookEvent | BookDeltaEvent | StatusEvent,
    Field(discriminator="kind"),
]


class CanonicalEvent(BaseModel):
    """A normalized market event from any venue.

    `(venue, external_market_id, sequence_no)` is the logical idempotency key.
    `market_id` is our internal UUID; the worker resolves it from the cached
    `(venue, external_id) -> market_id` map at ingestion time.
    """

    model_config = ConfigDict(frozen=True)

    venue: Venue
    external_market_id: str = Field(min_length=1)
    market_id: UUID
    sequence_no: int = Field(ge=0)
    event_ts: AwareDatetime
    ingest_ts: AwareDatetime
    payload: EventPayload
