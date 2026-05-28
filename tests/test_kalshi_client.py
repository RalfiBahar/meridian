"""KalshiClient: endpoint routing, response parsing, and error handling."""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from meridian.config import Settings
from meridian.kalshi import KalshiClient, KalshiMarketStatus
from meridian.kalshi.endpoints import REST_HOST, rest_base, signed_path, ws_url
from meridian.kalshi.errors import KalshiHttpError


def test_rest_base_routes_per_env() -> None:
    assert rest_base("demo") == "https://demo-api.kalshi.co/trade-api/v2"
    assert rest_base("prod") == "https://api.elections.kalshi.com/trade-api/v2"


def test_ws_url_routes_per_env() -> None:
    assert ws_url("demo") == "wss://demo-api.kalshi.co/trade-api/ws/v2"
    assert ws_url("prod") == "wss://api.elections.kalshi.com/trade-api/ws/v2"


def test_signed_path_includes_prefix() -> None:
    assert signed_path("/markets") == "/trade-api/v2/markets"
    assert signed_path("/markets/KX-FED-1/orderbook") == "/trade-api/v2/markets/KX-FED-1/orderbook"
    with pytest.raises(ValueError):
        signed_path("markets")  # missing leading slash


async def test_list_markets_parses_response(kalshi_settings: Settings) -> None:
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(
            200,
            json={
                "markets": [
                    {
                        "ticker": "KXFED-26JUN-T3.75",
                        "title": "Will Fed funds rate be above 3.75% in June 2026?",
                        "status": "active",
                        "yes_bid_dollars": "0.0200",
                        "yes_ask_dollars": "0.0300",
                        "no_bid_dollars": "0.9700",
                        "no_ask_dollars": "0.9800",
                        "last_price_dollars": "0.0250",
                        "yes_bid_size_fp": "1500.00",
                        "yes_ask_size_fp": "1200.00",
                        "volume_24h_fp": "98506.75",
                        "volume_fp": "150000.00",
                        "open_interest_fp": "42000.00",
                    }
                ],
                "cursor": "next-page-token",
            },
        )

    transport = httpx.MockTransport(handler)
    async with KalshiClient(kalshi_settings, transport=transport) as client:
        markets, cursor = await client.list_markets(limit=5, status="open")

    assert cursor == "next-page-token"
    assert len(markets) == 1
    m = markets[0]
    assert m.ticker == "KXFED-26JUN-T3.75"
    assert m.status is KalshiMarketStatus.ACTIVE
    assert m.yes_bid == Decimal("0.0200")
    assert m.yes_ask == Decimal("0.0300")
    assert m.yes_bid_size == Decimal("1500.00")
    assert m.volume_24h == Decimal("98506.75")

    req = captured["request"]
    assert req.url.path == "/trade-api/v2/markets"
    assert req.url.params["limit"] == "5"
    assert req.url.params["status"] == "open"
    assert req.url.host == "demo-api.kalshi.co"
    assert req.headers["KALSHI-ACCESS-KEY"] == "test-access-key"
    assert "KALSHI-ACCESS-SIGNATURE" in req.headers
    assert "KALSHI-ACCESS-TIMESTAMP" in req.headers


async def test_get_orderbook_parses_and_computes_best(kalshi_settings: Settings) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "orderbook_fp": {
                    "yes_dollars": [["0.0200", "1500.00"], ["0.0100", "5000.00"]],
                    "no_dollars": [["0.9700", "1200.00"], ["0.9600", "3000.00"]],
                }
            },
        )

    transport = httpx.MockTransport(handler)
    async with KalshiClient(kalshi_settings, transport=transport) as client:
        book = await client.get_orderbook("KXFED-26JUN-T3.75")

    assert book.yes_best_bid() == Decimal("0.0200")
    # Best YES ask = 1 - best NO bid (0.9700) = 0.0300.
    assert book.yes_best_ask() == Decimal("0.0300")
    assert book.yes_spread() == Decimal("0.0100")
    assert book.yes_total_size() == Decimal("6500.00")
    assert book.no_total_size() == Decimal("4200.00")


async def test_http_error_raises_kalshi_http_error(kalshi_settings: Settings) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="upstream exploded")

    transport = httpx.MockTransport(handler)
    async with KalshiClient(kalshi_settings, transport=transport) as client:
        with pytest.raises(KalshiHttpError) as exc_info:
            await client.list_markets(limit=1)

    assert exc_info.value.status == 500
    assert exc_info.value.path == "/trade-api/v2/markets"
    assert "upstream exploded" in exc_info.value.body


async def test_client_targets_demo_host_by_default(kalshi_settings: Settings) -> None:
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json={"markets": [], "cursor": None})

    transport = httpx.MockTransport(handler)
    async with KalshiClient(kalshi_settings, transport=transport) as client:
        await client.list_markets(limit=1)

    assert captured["request"].url.host == REST_HOST["demo"].replace("https://", "")
