"""Async REST client for the Polymarket CLOB API.

No authentication: the market-data endpoints below are public. See
`docs/polymarket.md`.
"""

from __future__ import annotations

from types import TracebackType
from typing import Any, Self

import httpx

from meridian.config import Settings
from meridian.logging import get_logger
from meridian.polymarket.endpoints import REST_BASE
from meridian.polymarket.errors import PolymarketHttpError
from meridian.polymarket.models import PolymarketMarket, PolymarketOrderbook

# Polymarket signals "no more pages" by looping `next_cursor` back to this
# base64-encoded sentinel (`-1`) rather than an empty/absent value.
END_CURSOR = "LTE="


class PolymarketClient:
    """Async client for Polymarket's CLOB REST API.

    Use as an async context manager:

        async with PolymarketClient(settings) as client:
            markets, cursor = await client.list_markets()
    """

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=REST_BASE,
            transport=transport,
            timeout=settings.polymarket_request_timeout,
        )
        self._log = get_logger("meridian.polymarket.client")

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
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resp = await self._client.request(method, path, params=params)
        if resp.status_code >= 400:
            raise PolymarketHttpError(
                status=resp.status_code,
                method=method,
                path=path,
                body=resp.text,
            )
        data: dict[str, Any] = resp.json()
        return data

    async def list_markets(
        self,
        *,
        next_cursor: str = "",
    ) -> tuple[list[PolymarketMarket], str | None]:
        """List markets, one page at a time.

        Pass the returned cursor back in as `next_cursor` to page forward.
        Returns `(markets, None)` once `next_cursor` reaches `END_CURSOR`.
        """
        data = await self._request("GET", "/markets", params={"next_cursor": next_cursor})
        markets = [PolymarketMarket.model_validate(m) for m in data.get("data", [])]
        cursor = data.get("next_cursor")
        return markets, (None if cursor in (None, END_CURSOR) else cursor)

    async def get_market(self, condition_id: str) -> PolymarketMarket:
        data = await self._request("GET", f"/markets/{condition_id}")
        return PolymarketMarket.model_validate(data)

    async def get_orderbook(self, token_id: str) -> PolymarketOrderbook:
        data = await self._request("GET", "/book", params={"token_id": token_id})
        return PolymarketOrderbook.model_validate(data)
