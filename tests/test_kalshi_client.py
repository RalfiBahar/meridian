"""KalshiClient: endpoint routing, response parsing, and error handling."""

from __future__ import annotations

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
                        "ticker": "KX-TEST-1",
                        "title": "Will Meridian ship Phase 1 on time?",
                        "status": "active",
                        "yes_bid": 65,
                        "yes_ask": 67,
                        "last_price": 66,
                        "volume_24h": 12345,
                    }
                ],
                "cursor": "next-page-token",
            },
        )

    transport = httpx.MockTransport(handler)
    async with KalshiClient(kalshi_settings, transport=transport) as client:
        markets, cursor = await client.list_markets(limit=5, status=KalshiMarketStatus.ACTIVE)

    assert cursor == "next-page-token"
    assert len(markets) == 1
    m = markets[0]
    assert m.ticker == "KX-TEST-1"
    assert m.status is KalshiMarketStatus.ACTIVE
    assert m.yes_bid == 65 and m.yes_ask == 67

    req = captured["request"]
    assert req.url.path == "/trade-api/v2/markets"
    assert req.url.params["limit"] == "5"
    assert req.url.params["status"] == "active"
    assert req.url.host == "demo-api.kalshi.co"
    assert req.headers["KALSHI-ACCESS-KEY"] == "test-access-key"
    assert "KALSHI-ACCESS-SIGNATURE" in req.headers
    assert "KALSHI-ACCESS-TIMESTAMP" in req.headers


async def test_get_orderbook_parses_and_computes_best(kalshi_settings: Settings) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "orderbook": {
                    "yes": [[62, 800], [60, 2000]],  # bids on YES
                    "no": [[34, 500], [33, 1500]],  # bids on NO
                }
            },
        )

    transport = httpx.MockTransport(handler)
    async with KalshiClient(kalshi_settings, transport=transport) as client:
        book = await client.get_orderbook("KX-TEST-1")

    assert book.yes_best_bid_cents() == 62
    # Best YES ask = 100 - best NO bid (34) = 66.
    assert book.yes_best_ask_cents() == 66
    assert book.yes_total_size() == 2800
    assert book.no_total_size() == 2000


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
