"""Typed Pydantic models for Kalshi REST responses.

Models use `extra="allow"` so we don't reject responses when Kalshi adds new
fields; the raw payload remains accessible via `model_dump()`.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class KalshiMarketStatus(StrEnum):
    INITIALIZED = "initialized"
    ACTIVE = "active"
    CLOSED = "closed"
    SETTLED = "settled"
    DEACTIVATED = "deactivated"


class KalshiMarket(BaseModel):
    """One tradable Kalshi contract as returned by `/markets/{ticker}` or `/markets`.

    Prices (`yes_bid`, `yes_ask`, etc.) are integer cents in [1, 99]. Settlement
    flips them to 0 or 100.
    """

    model_config = ConfigDict(extra="allow")

    ticker: str
    event_ticker: str | None = None
    title: str | None = None
    subtitle: str | None = None
    category: str | None = None
    status: KalshiMarketStatus | None = None
    open_time: AwareDatetime | None = None
    close_time: AwareDatetime | None = None
    expiration_time: AwareDatetime | None = None
    yes_bid: int | None = Field(default=None, ge=0, le=100)
    yes_ask: int | None = Field(default=None, ge=0, le=100)
    no_bid: int | None = Field(default=None, ge=0, le=100)
    no_ask: int | None = Field(default=None, ge=0, le=100)
    last_price: int | None = Field(default=None, ge=0, le=100)
    volume: int | None = None
    volume_24h: int | None = None
    open_interest: int | None = None
    liquidity: int | None = None


class KalshiOrderbook(BaseModel):
    """Order book for one market.

    Kalshi returns:
        {"yes": [[price_cents, size], ...], "no": [[price_cents, size], ...]}

    `yes` is bids on the YES side; `no` is bids on the NO side. The ask on YES
    is inferred from the best NO bid as (100 - best_no_bid).
    """

    model_config = ConfigDict(extra="allow")

    yes: list[tuple[int, int]] = Field(default_factory=list)
    no: list[tuple[int, int]] = Field(default_factory=list)

    def yes_best_bid_cents(self) -> int | None:
        if not self.yes:
            return None
        return max(level[0] for level in self.yes)

    def yes_best_ask_cents(self) -> int | None:
        """Best YES ask = 100 - best NO bid."""
        if not self.no:
            return None
        return 100 - max(level[0] for level in self.no)

    def yes_total_size(self) -> int:
        return sum(level[1] for level in self.yes)

    def no_total_size(self) -> int:
        return sum(level[1] for level in self.no)
