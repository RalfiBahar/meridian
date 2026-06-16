"""PolymarketClient: endpoint routing, response parsing, and error handling."""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from meridian.config import Settings
from meridian.polymarket import PolymarketClient
from meridian.polymarket.client import END_CURSOR
from meridian.polymarket.errors import PolymarketHttpError


async def test_list_markets_parses_response(settings: Settings) -> None:
    captured: dict[str, httpx.Request] = {}

    condition_id = "0x5eed579ff6763914d78a966c83473ba2485ac8910d0a0914eef6d9fcb33085de"

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "condition_id": condition_id,
                        "question": "NCAAB: Arizona State vs. Nevada",
                        "market_slug": "ncaab-arst-nev",
                        "active": True,
                        "closed": True,
                        "accepting_orders": False,
                        "minimum_tick_size": 0.01,
                        "tokens": [
                            {
                                "token_id": "111",
                                "outcome": "Arizona State",
                                "price": 1,
                                "winner": True,
                            },
                            {"token_id": "222", "outcome": "Nevada", "price": 0, "winner": False},
                        ],
                    }
                ],
                "next_cursor": "MTAwMA==",
                "limit": 1000,
                "count": 1,
            },
        )

    transport = httpx.MockTransport(handler)
    async with PolymarketClient(settings, transport=transport) as client:
        markets, cursor = await client.list_markets()

    assert cursor == "MTAwMA=="
    assert len(markets) == 1
    m = markets[0]
    assert m.condition_id == condition_id
    assert m.minimum_tick_size == Decimal("0.01")
    assert len(m.tokens) == 2
    assert m.tokens[0].token_id == "111"
    assert m.tokens[0].winner is True

    req = captured["request"]
    assert req.url.path == "/markets"
    assert req.url.params["next_cursor"] == ""


async def test_list_markets_returns_none_cursor_at_end(settings: Settings) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [], "next_cursor": END_CURSOR})

    transport = httpx.MockTransport(handler)
    async with PolymarketClient(settings, transport=transport) as client:
        markets, cursor = await client.list_markets(next_cursor="MTAwMA==")

    assert markets == []
    assert cursor is None


async def test_get_orderbook_parses_levels_and_computes_best(settings: Settings) -> None:
    asset_id = "98022490269692409998126496127597032490334070080325855126491859374983463996227"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "market": "0x1fad72fae204143ff1c3035e99e7c0f65ea8d5cd9bd1070987bd1a3316f772be",
                "asset_id": asset_id,
                "timestamp": "1781586414352",
                "hash": "6ddb005ee385ee5d7f90d8d55b6755ad5955bc10",
                "bids": [{"price": "0.01", "size": "2462.11"}, {"price": "0.02", "size": "14.29"}],
                "asks": [{"price": "0.52", "size": "25"}, {"price": "0.50", "size": "60"}],
            },
        )

    transport = httpx.MockTransport(handler)
    async with PolymarketClient(settings, transport=transport) as client:
        book = await client.get_orderbook(asset_id)

    assert book.best_bid() == Decimal("0.02")
    assert book.best_ask() == Decimal("0.50")
    assert book.spread() == Decimal("0.48")
    assert book.total_bid_size() == Decimal("2476.40")
    assert book.total_ask_size() == Decimal("85")


async def test_get_orderbook_handles_missing_book(settings: Settings) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "No orderbook exists for the requested token id"})

    transport = httpx.MockTransport(handler)
    async with PolymarketClient(settings, transport=transport) as client:
        book = await client.get_orderbook("closed-token")

    assert book.bids == []
    assert book.asks == []
    assert book.best_bid() is None


async def test_get_market_unwraps_single_object(settings: Settings) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "condition_id": "0xabc",
                "question": "Will X happen?",
                "tokens": [{"token_id": "1", "outcome": "Yes"}],
            },
        )

    transport = httpx.MockTransport(handler)
    async with PolymarketClient(settings, transport=transport) as client:
        market = await client.get_market("0xabc")

    assert market.condition_id == "0xabc"
    assert market.question == "Will X happen?"


async def test_http_error_raises_polymarket_http_error(settings: Settings) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="upstream exploded")

    transport = httpx.MockTransport(handler)
    async with PolymarketClient(settings, transport=transport) as client:
        with pytest.raises(PolymarketHttpError) as exc_info:
            await client.list_markets()

    assert exc_info.value.status == 500
    assert exc_info.value.path == "/markets"
    assert "upstream exploded" in exc_info.value.body
