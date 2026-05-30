"""Kalshi WebSocket client.

Connects to the env-routed WS endpoint with a signed handshake, subscribes
to one or more channels for a set of market tickers, and exposes an async
iterator over received messages.

Phase 1c.1 scope: minimal, read-only. No reconnect logic, no idempotent
persistence — those layers come in 1c.2 and 1c.3.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from types import TracebackType
from typing import Any, Self, cast

import websockets
from websockets.asyncio.client import ClientConnection

from meridian.config import Settings
from meridian.kalshi.auth import KalshiSigner
from meridian.kalshi.endpoints import WS_PATH, ws_url
from meridian.logging import get_logger

DEFAULT_CHANNELS: tuple[str, ...] = (
    "orderbook_delta",
    "trade",
    "ticker",
    "market_lifecycle_v2",
)


class KalshiWebSocketClient:
    """Async-context-managed Kalshi WS client.

    Usage:

        async with KalshiWebSocketClient(settings, tickers=[...]) as client:
            async for msg in client.stream():
                ...   # msg is a parsed dict
    """

    def __init__(
        self,
        settings: Settings,
        *,
        tickers: list[str],
        channels: tuple[str, ...] = DEFAULT_CHANNELS,
        subscribe_id: int = 1,
    ) -> None:
        if not tickers:
            raise ValueError("tickers must be non-empty")
        self._settings = settings
        self._tickers = list(tickers)
        self._channels = tuple(channels)
        self._subscribe_id = subscribe_id
        self._signer = KalshiSigner.from_settings(settings)
        self._ws: ClientConnection | None = None
        self._log = get_logger("meridian.kalshi.ws")

    async def __aenter__(self) -> Self:
        headers = self._signer.sign("GET", WS_PATH)
        url = ws_url(self._settings.kalshi_env)
        self._log.info("ws.connecting", url=url, tickers=len(self._tickers))
        self._ws = await websockets.connect(url, additional_headers=headers)
        await self._send_subscribe()
        self._log.info(
            "ws.subscribed",
            channels=list(self._channels),
            tickers=len(self._tickers),
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def _send_subscribe(self) -> None:
        assert self._ws is not None
        payload: dict[str, Any] = {
            "id": self._subscribe_id,
            "cmd": "subscribe",
            "params": {
                "channels": list(self._channels),
                "market_tickers": self._tickers,
            },
        }
        await self._ws.send(json.dumps(payload))

    async def stream(self) -> AsyncIterator[dict[str, Any]]:
        """Yield parsed JSON messages until the connection closes."""
        if self._ws is None:
            raise RuntimeError("KalshiWebSocketClient must be used as an async context manager")
        async for raw in self._ws:
            text = raw if isinstance(raw, str) else raw.decode("utf-8")
            yield cast(dict[str, Any], json.loads(text))
