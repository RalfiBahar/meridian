"""Typed Pydantic models for Kalshi REST responses.

The Kalshi API uses two naming conventions on the wire:

- `*_dollars` — decimal-string prices in USD (e.g. `"0.0200"`)
- `*_fp`      — floating-point quantities as decimal strings (e.g. `"19.00"`)

We expose those as Pythonic attribute names via Pydantic aliases, so
downstream code reads `market.yes_bid` rather than `market.yes_bid_dollars`.
`populate_by_name=True` lets test fixtures construct models using either form.

`extra="allow"` keeps the raw payload accessible via `model_dump()` and means
Kalshi can add new response fields without breaking us.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class KalshiMarketStatus(StrEnum):
    """Values reported in the market's `status` *response* field.

    Note: these differ from the query parameter `?status=` filter values
    (`unopened|open|closed|settled`).
    """

    INITIALIZED = "initialized"
    ACTIVE = "active"
    CLOSED = "closed"
    SETTLED = "settled"
    DEACTIVATED = "deactivated"


class KalshiMarket(BaseModel):
    """One tradable Kalshi contract.

    Prices are exact `Decimal` USD amounts in [0, 1]; at settlement they
    flip to exactly 0 or 1.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    ticker: str
    event_ticker: str | None = None
    title: str | None = None
    subtitle: str | None = None
    yes_sub_title: str | None = None
    no_sub_title: str | None = None
    market_type: str | None = None
    category: str | None = None
    status: KalshiMarketStatus | None = None

    open_time: AwareDatetime | None = None
    close_time: AwareDatetime | None = None
    expiration_time: AwareDatetime | None = None

    yes_bid: Decimal | None = Field(default=None, alias="yes_bid_dollars")
    yes_ask: Decimal | None = Field(default=None, alias="yes_ask_dollars")
    no_bid: Decimal | None = Field(default=None, alias="no_bid_dollars")
    no_ask: Decimal | None = Field(default=None, alias="no_ask_dollars")
    last_price: Decimal | None = Field(default=None, alias="last_price_dollars")
    previous_yes_bid: Decimal | None = Field(default=None, alias="previous_yes_bid_dollars")
    previous_yes_ask: Decimal | None = Field(default=None, alias="previous_yes_ask_dollars")
    liquidity: Decimal | None = Field(default=None, alias="liquidity_dollars")
    notional_value: Decimal | None = Field(default=None, alias="notional_value_dollars")

    yes_bid_size: Decimal | None = Field(default=None, alias="yes_bid_size_fp")
    yes_ask_size: Decimal | None = Field(default=None, alias="yes_ask_size_fp")
    volume: Decimal | None = Field(default=None, alias="volume_fp")
    volume_24h: Decimal | None = Field(default=None, alias="volume_24h_fp")
    open_interest: Decimal | None = Field(default=None, alias="open_interest_fp")


class KalshiOrderbook(BaseModel):
    """L2 order book for one Kalshi market.

    Kalshi returns the book under the `orderbook_fp` key:

        {"orderbook_fp": {
            "yes_dollars": [[price_str, size_str], ...],
            "no_dollars":  [[price_str, size_str], ...],
        }}

    `yes_dollars` lists bids on the YES side; `no_dollars` lists bids on the
    NO side. Asks are not posted directly: the YES ask is inferred from the
    best NO bid as `1 - best_no_bid` (and symmetrically for the NO ask).
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    yes: list[tuple[Decimal, Decimal]] = Field(default_factory=list, alias="yes_dollars")
    no: list[tuple[Decimal, Decimal]] = Field(default_factory=list, alias="no_dollars")

    def yes_best_bid(self) -> Decimal | None:
        if not self.yes:
            return None
        return max(level[0] for level in self.yes)

    def yes_best_ask(self) -> Decimal | None:
        """Best YES ask = 1 - best NO bid."""
        if not self.no:
            return None
        return Decimal(1) - max(level[0] for level in self.no)

    def yes_total_size(self) -> Decimal:
        return sum((level[1] for level in self.yes), start=Decimal(0))

    def no_total_size(self) -> Decimal:
        return sum((level[1] for level in self.no), start=Decimal(0))

    def yes_spread(self) -> Decimal | None:
        bid = self.yes_best_bid()
        ask = self.yes_best_ask()
        if bid is None or ask is None:
            return None
        return ask - bid
