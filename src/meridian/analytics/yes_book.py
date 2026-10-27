"""YES-probability view of a stored order book.

Polymarket snapshots use sides ``bid`` and ``ask``. Those prices are already
the price of the outcome token, in dollars per one-dollar payoff.

Kalshi snapshots use sides ``yes`` and ``no``, and both are bids. A YES bid
at ``p`` is a bid to buy YES at ``p``. A NO bid at ``q`` is a bid to buy NO
at ``q``, which is the same order as a YES ask at ``1 - q`` with the same
size. Kalshi does not post asks as their own levels.

A snapshot that mixes the two conventions is rejected. Complementing a
Polymarket ask would turn a real YES ask into a bid.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

_ONE = Decimal(1)
_POLY_SIDES = frozenset({"bid", "ask"})
_KALSHI_SIDES = frozenset({"yes", "no"})

Level = tuple[str, Decimal, Decimal]


class BookConventionError(ValueError):
    """Book levels mix venues or use a side this converter does not know."""


@dataclass(frozen=True)
class YesTop:
    """Best YES bid and YES ask after venue-specific conversion."""

    bid: Decimal | None
    ask: Decimal | None
    bid_size: Decimal | None
    ask_size: Decimal | None

    @property
    def spread(self) -> Decimal | None:
        if self.bid is None or self.ask is None:
            return None
        return self.ask - self.bid


@dataclass(frozen=True)
class YesFill:
    """Walk of a YES bid or YES ask ladder."""

    filled_quantity: Decimal
    avg_fill_price: Decimal | None
    best_quote: Decimal | None
    slippage: Decimal | None
    n_levels_consumed: int
    partially_filled: bool


def split_yes_book(
    levels: Sequence[Level],
) -> tuple[list[tuple[Decimal, Decimal]], list[tuple[Decimal, Decimal]]]:
    """Return ``(yes_bids, yes_asks)`` as ``(price, size)`` pairs.

    Prices on both lists are YES probabilities. Kalshi NO bids are converted
    with ``yes_ask = 1 - no_bid``. Polymarket bid and ask prices are unchanged.
    """
    sides = {side for side, _, _ in levels}
    if not sides:
        return [], []
    unknown = sides - _POLY_SIDES - _KALSHI_SIDES
    if unknown:
        raise BookConventionError(f"unknown book sides: {sorted(unknown)}")
    if sides & _POLY_SIDES and sides & _KALSHI_SIDES:
        raise BookConventionError(
            "refusing to convert a book that mixes Polymarket bid/ask with Kalshi yes/no"
        )

    bids: list[tuple[Decimal, Decimal]] = []
    asks: list[tuple[Decimal, Decimal]] = []
    if sides & _KALSHI_SIDES:
        for side, price, size in levels:
            if side == "yes":
                bids.append((price, size))
            elif side == "no":
                asks.append((_ONE - price, size))
        return bids, asks

    for side, price, size in levels:
        if side == "bid":
            bids.append((price, size))
        elif side == "ask":
            asks.append((price, size))
    return bids, asks


def _aggregate_best(
    levels: Sequence[tuple[Decimal, Decimal]],
    *,
    highest: bool,
) -> tuple[Decimal, Decimal] | None:
    positive = [(price, size) for price, size in levels if size > 0]
    if not positive:
        return None
    price = max(p for p, _ in positive) if highest else min(p for p, _ in positive)
    size = sum((s for p, s in positive if p == price), Decimal(0))
    return price, size


def yes_top_of_book(levels: Sequence[Level]) -> YesTop:
    """Best YES bid (highest) and best YES ask (lowest), with size at that price."""
    bids, asks = split_yes_book(levels)
    bid = _aggregate_best(bids, highest=True)
    ask = _aggregate_best(asks, highest=False)
    return YesTop(
        bid=None if bid is None else bid[0],
        ask=None if ask is None else ask[0],
        bid_size=None if bid is None else bid[1],
        ask_size=None if ask is None else ask[1],
    )


def depth_weighted_yes_probability(levels: Sequence[Level]) -> Decimal | None:
    """Average of the size-weighted YES bid and the size-weighted YES ask.

    Requires at least one positive-size level on each side. Returns None for
    an empty book, a one-sided book, or a mixed-convention book.
    """
    try:
        bids, asks = split_yes_book(levels)
    except BookConventionError:
        return None
    weighted_bid = _size_weighted_price(bids)
    weighted_ask = _size_weighted_price(asks)
    if weighted_bid is None or weighted_ask is None:
        return None
    return (weighted_bid + weighted_ask) / 2


def _size_weighted_price(levels: Sequence[tuple[Decimal, Decimal]]) -> Decimal | None:
    total = sum((size for _, size in levels if size > 0), Decimal(0))
    if total == 0:
        return None
    weighted = sum((price * size for price, size in levels if size > 0), Decimal(0))
    return weighted / total


def simulate_yes_fill(
    levels: Sequence[Level],
    *,
    side: str,
    target_quantity: Decimal,
) -> YesFill:
    """Walk YES asks for a buy, or YES bids for a sell.

    Buy slippage is ``avg_fill - best_ask`` (positive means a worse price).
    Sell slippage is ``best_bid - avg_fill`` (positive means a worse price).
    Kalshi NO bids are converted to YES asks before the walk, then sorted by
    the YES price. The raw NO price is not an executable YES price.
    """
    if side not in ("buy", "sell"):
        raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")
    if target_quantity <= 0:
        raise ValueError("target_quantity must be positive")

    bids, asks = split_yes_book(levels)
    raw = asks if side == "buy" else bids
    ladder = [(price, size) for price, size in raw if size > 0]
    ladder.sort(key=lambda level: level[0], reverse=(side == "sell"))
    if not ladder:
        return YesFill(
            filled_quantity=Decimal(0),
            avg_fill_price=None,
            best_quote=None,
            slippage=None,
            n_levels_consumed=0,
            partially_filled=True,
        )

    best_quote = ladder[0][0]
    remaining = target_quantity
    total_cost = Decimal(0)
    filled = Decimal(0)
    n_levels = 0
    for price, size in ladder:
        if remaining <= 0:
            break
        take = min(remaining, size)
        total_cost += take * price
        filled += take
        remaining -= take
        n_levels += 1

    avg_fill = total_cost / filled if filled > 0 else None
    slippage = None if avg_fill is None else avg_fill - best_quote
    if side == "sell" and slippage is not None:
        slippage = -slippage
    return YesFill(
        filled_quantity=filled,
        avg_fill_price=avg_fill,
        best_quote=best_quote,
        slippage=slippage,
        n_levels_consumed=n_levels,
        partially_filled=remaining > 0,
    )
