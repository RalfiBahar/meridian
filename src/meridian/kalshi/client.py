"""Async REST client for the Kalshi exchange API."""

from __future__ import annotations

from types import TracebackType
from typing import Any, Self

import httpx

from meridian.config import Settings
from meridian.kalshi.auth import KalshiSigner
from meridian.kalshi.endpoints import REST_HOST, signed_path
from meridian.kalshi.errors import KalshiHttpError
from meridian.kalshi.models import KalshiMarket, KalshiMarketStatus, KalshiOrderbook
from meridian.logging import get_logger


class KalshiClient:
    """Async client for Kalshi's REST API.

    Use as an async context manager:

        async with KalshiClient(settings) as client:
            markets, cursor = await client.list_markets(limit=10)
    """

    def __init__(
        self,
        settings: Settings,
        *,
        signer: KalshiSigner | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._signer = signer or KalshiSigner.from_settings(settings)
        self._base_host = REST_HOST[settings.kalshi_env]
        self._client = httpx.AsyncClient(
            transport=transport,
            timeout=settings.kalshi_request_timeout,
        )
        self._log = get_logger("meridian.kalshi.client").bind(env=settings.kalshi_env)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        relative_path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        path = signed_path(relative_path)
        url = self._base_host + path
        headers = self._signer.sign(method, path)
        resp = await self._client.request(method, url, params=params, headers=headers)
        if resp.status_code >= 400:
            raise KalshiHttpError(
                status=resp.status_code,
                method=method,
                path=path,
                body=resp.text,
            )
        data: dict[str, Any] = resp.json()
        return data

    async def get_exchange_status(self) -> dict[str, Any]:
        return await self._request("GET", "/exchange/status")

    async def list_markets(
        self,
        *,
        limit: int = 100,
        cursor: str | None = None,
        status: KalshiMarketStatus | None = None,
        event_ticker: str | None = None,
    ) -> tuple[list[KalshiMarket], str | None]:
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        if status is not None:
            params["status"] = status.value
        if event_ticker:
            params["event_ticker"] = event_ticker
        data = await self._request("GET", "/markets", params=params)
        markets = [KalshiMarket.model_validate(m) for m in data.get("markets", [])]
        next_cursor = data.get("cursor") or None
        return markets, next_cursor

    async def get_market(self, ticker: str) -> KalshiMarket:
        data = await self._request("GET", f"/markets/{ticker}")
        return KalshiMarket.model_validate(data["market"])

    async def get_orderbook(self, ticker: str) -> KalshiOrderbook:
        data = await self._request("GET", f"/markets/{ticker}/orderbook")
        return KalshiOrderbook.model_validate(data.get("orderbook", {}))
