"""Tests for live settled-market backfill helpers."""

from meridian.ingest.settled_backfill import p_mid_from_candlestick, settled_value_from_market


def test_settled_value_from_result_yes() -> None:
    assert settled_value_from_market({"result": "yes"}) == 1


def test_settled_value_from_result_no() -> None:
    assert settled_value_from_market({"result": "no"}) == 0


def test_settled_value_from_settlement_dollars() -> None:
    assert settled_value_from_market({"settlement_value_dollars": "1.0000"}) == 1
    assert settled_value_from_market({"settlement_value_dollars": "0.0000"}) == 0


def test_p_mid_from_candlestick_bid_ask() -> None:
    candle = {
        "yes_bid": {"close_dollars": "0.40"},
        "yes_ask": {"close_dollars": "0.60"},
    }
    assert p_mid_from_candlestick(candle) == 0.5


def test_p_mid_from_candlestick_price_fallback() -> None:
    candle = {"price": {"mean_dollars": "0.72"}}
    assert p_mid_from_candlestick(candle) == 0.72
