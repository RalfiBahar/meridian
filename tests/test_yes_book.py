"""Hand-checked YES/NO conversions. No database."""

from decimal import Decimal

import pytest

from meridian.analytics.signals import _microprice
from meridian.analytics.yes_book import (
    BookConventionError,
    depth_weighted_yes_probability,
    simulate_yes_fill,
    yes_top_of_book,
)


def test_kalshi_no_bid_is_a_yes_ask_at_one_minus_price() -> None:
    levels = [
        ("yes", Decimal("0.40"), Decimal("100")),
        ("no", Decimal("0.55"), Decimal("50")),
    ]
    top = yes_top_of_book(levels)
    assert top.bid == Decimal("0.40")
    assert top.bid_size == Decimal("100")
    # Best NO bid 0.55 is a YES ask at 0.45, not an ask at 0.55.
    assert top.ask == Decimal("0.45")
    assert top.ask_size == Decimal("50")
    assert top.spread == Decimal("0.05")
    micro = _microprice(top.bid, top.ask, top.bid_size, top.ask_size)
    # (0.40 * 50 + 0.45 * 100) / 150 = 65 / 150
    assert micro == Decimal("65") / Decimal("150")


def test_polymarket_ask_is_not_complemented() -> None:
    levels = [
        ("bid", Decimal("0.40"), Decimal("100")),
        ("ask", Decimal("0.45"), Decimal("50")),
    ]
    top = yes_top_of_book(levels)
    assert top.ask == Decimal("0.45")
    depth = depth_weighted_yes_probability(levels)
    assert depth == Decimal("0.425")


def test_mixed_venue_sides_are_rejected() -> None:
    levels = [
        ("bid", Decimal("0.40"), Decimal("10")),
        ("no", Decimal("0.50"), Decimal("10")),
    ]
    with pytest.raises(BookConventionError):
        yes_top_of_book(levels)
    assert depth_weighted_yes_probability(levels) is None


def test_buy_walks_complemented_no_bids_from_the_best_yes_ask() -> None:
    # NO 0.60 size 10 -> YES ask 0.40
    # NO 0.50 size 10 -> YES ask 0.50
    levels = [
        ("no", Decimal("0.50"), Decimal("10")),
        ("no", Decimal("0.60"), Decimal("10")),
    ]
    fill = simulate_yes_fill(levels, side="buy", target_quantity=Decimal("15"))
    assert fill.best_quote == Decimal("0.40")
    assert fill.filled_quantity == Decimal("15")
    # 10 @ 0.40 + 5 @ 0.50 = 6.50, average 6.50/15
    assert fill.avg_fill_price == Decimal("6.50") / Decimal("15")
    assert fill.slippage == Decimal("6.50") / Decimal("15") - Decimal("0.40")
    assert fill.n_levels_consumed == 2
    assert not fill.partially_filled


def test_sell_walks_yes_bids_down() -> None:
    levels = [
        ("yes", Decimal("0.48"), Decimal("10")),
        ("yes", Decimal("0.47"), Decimal("10")),
    ]
    fill = simulate_yes_fill(levels, side="sell", target_quantity=Decimal("15"))
    assert fill.best_quote == Decimal("0.48")
    # 10 @ 0.48 + 5 @ 0.47 = 7.15, average 7.15/15
    assert fill.avg_fill_price == Decimal("7.15") / Decimal("15")
    # Positive slippage means the fill is worse than the best bid.
    assert fill.slippage == Decimal("0.48") - (Decimal("7.15") / Decimal("15"))
