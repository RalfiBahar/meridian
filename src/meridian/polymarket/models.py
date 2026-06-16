"""Typed Pydantic models for Polymarket CLOB REST responses.

Unlike Kalshi, prices and sizes are bare decimal strings/numbers with no
`_dollars`/`_fp` suffix convention. `extra="allow"` keeps the raw payload
accessible via `model_dump()` and means Polymarket can add new response
fields without breaking us.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class PolymarketToken(BaseModel):
    """One outcome of a market — the unit that actually has an order book."""

    model_config = ConfigDict(extra="allow")

    token_id: str
    outcome: str
    price: Decimal | None = None
    winner: bool | None = None


class PolymarketMarket(BaseModel):
    """One Polymarket question, grouping one order book per outcome token.

    See `docs/polymarket.md` for the `condition_id` vs `token_id` distinction.
    """

    model_config = ConfigDict(extra="allow")

    condition_id: str
    question_id: str | None = None
    question: str
    description: str | None = None
    market_slug: str | None = None
    end_date_iso: AwareDatetime | None = None
    active: bool = False
    closed: bool = False
    accepting_orders: bool = False
    enable_order_book: bool = False
    minimum_tick_size: Decimal | None = None
    minimum_order_size: Decimal | None = None
    neg_risk: bool = False
    tokens: list[PolymarketToken] = Field(default_factory=list)


class PolymarketLevel(BaseModel):
    """One `{"price": ..., "size": ...}` book-level object.

    Polymarket reports levels as objects, not `[price, size]` tuples like
    Kalshi — hence this thin wrapper instead of a `tuple[Decimal, Decimal]`.
    """

    model_config = ConfigDict(extra="allow")

    price: Decimal
    size: Decimal


class PolymarketOrderbook(BaseModel):
    """L2 order book for one Polymarket token (outcome).

    Returned by `GET /book?token_id=...` and matches the WS `book` event
    shape (minus `event_type`). A token with no live book returns
    `{"error": "..."}` with no `bids`/`asks` keys — those default to `[]`.
    """

    model_config = ConfigDict(extra="allow")

    market: str | None = None
    asset_id: str | None = None
    timestamp: int | None = None
    hash: str | None = None
    bids: list[PolymarketLevel] = Field(default_factory=list)
    asks: list[PolymarketLevel] = Field(default_factory=list)

    def best_bid(self) -> Decimal | None:
        if not self.bids:
            return None
        return max(level.price for level in self.bids)

    def best_ask(self) -> Decimal | None:
        if not self.asks:
            return None
        return min(level.price for level in self.asks)

    def spread(self) -> Decimal | None:
        bid = self.best_bid()
        ask = self.best_ask()
        if bid is None or ask is None:
            return None
        return ask - bid

    def total_bid_size(self) -> Decimal:
        return sum((level.size for level in self.bids), start=Decimal(0))

    def total_ask_size(self) -> Decimal:
        return sum((level.size for level in self.asks), start=Decimal(0))
