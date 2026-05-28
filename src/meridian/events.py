"""Canonical cross-venue event models.

Every venue-specific parser normalizes its raw messages into a CanonicalEvent
before any downstream consumer touches them. This keeps the rest of the
system venue-agnostic.
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
    STATUS = "status"


PriceDecimal = Annotated[Decimal, Field(ge=Decimal(0), le=Decimal(1))]


class _Payload(BaseModel):
    """Common config for payload variants: frozen + reject unknown fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class QuoteEvent(_Payload):
    kind: Literal[EventKind.QUOTE]
    bid: PriceDecimal | None = None
    ask: PriceDecimal | None = None
    bid_size: int | None = Field(default=None, ge=0)
    ask_size: int | None = Field(default=None, ge=0)


class TradeEvent(_Payload):
    kind: Literal[EventKind.TRADE]
    price: PriceDecimal
    size: int = Field(ge=1)
    aggressor: Literal["buy", "sell"] | None = None


class BookLevel(BaseModel):
    model_config = ConfigDict(frozen=True)
    side: Literal["bid", "ask"]
    level: int = Field(ge=0)
    price: PriceDecimal
    size: int = Field(ge=0)


class BookEvent(_Payload):
    kind: Literal[EventKind.BOOK]
    levels: list[BookLevel]


class StatusEvent(_Payload):
    kind: Literal[EventKind.STATUS]
    status: Literal["open", "halted", "closed", "settled"]


EventPayload = Annotated[
    QuoteEvent | TradeEvent | BookEvent | StatusEvent,
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
