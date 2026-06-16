"""Polymarket CLOB market-data WebSocket client.

Connects to the public market channel (no auth), subscribes to one or more
token (asset) IDs, and exposes an async iterator over received messages.

Two things make this client shaped differently from `KalshiWebSocketClient`:

1. The server sometimes batches several messages into one JSON array frame
   (observed on the initial book snapshot burst); `stream()` flattens these
   so callers always see one message dict per yield.
2. The server requires a client-sent `PING` text frame roughly every 10s or
   it disconnects; `__aenter__` starts a background task for this.

`custom_feature_enabled` defaults to `False`: when `True`, Polymarket also
broadcasts a `new_market` event for *every* newly created market platform-
wide, regardless of the subscribed asset IDs — pure noise for an ingestion
worker that only cares about the markets it subscribed to.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from types import TracebackType
from typing import Any, Self, cast

import websockets
from websockets.asyncio.client import ClientConnection

from meridian.logging import get_logger
from meridian.polymarket.endpoints import PING_INTERVAL_SECONDS, PING_TEXT, WS_URL


class PolymarketWebSocketClient:
    """Async-context-managed Polymarket market-channel WS client.

    Usage:

        async with PolymarketWebSocketClient(asset_ids=[...]) as client:
            async for msg in client.stream():
                ...   # msg is a parsed dict
    """

    def __init__(
        self,
        *,
        asset_ids: list[str],
        custom_feature_enabled: bool = False,
    ) -> None:
        if not asset_ids:
            raise ValueError("asset_ids must be non-empty")
        self._asset_ids = list(asset_ids)
        self._custom_feature_enabled = custom_feature_enabled
        self._ws: ClientConnection | None = None
        self._ping_task: asyncio.Task[None] | None = None
        self._log = get_logger("meridian.polymarket.ws")

    async def __aenter__(self) -> Self:
        self._log.info("ws.connecting", url=WS_URL, assets=len(self._asset_ids))
        self._ws = await websockets.connect(WS_URL)
        await self._send_subscribe()
        self._ping_task = asyncio.create_task(self._ping_loop())
        self._log.info("ws.subscribed", assets=len(self._asset_ids))
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._ping_task is not None:
            self._ping_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._ping_task
            self._ping_task = None
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def _send_subscribe(self) -> None:
        assert self._ws is not None
        payload: dict[str, Any] = {
            "assets_ids": self._asset_ids,
            "type": "market",
        }
        if self._custom_feature_enabled:
            payload["custom_feature_enabled"] = True
        await self._ws.send(json.dumps(payload))

    async def _ping_loop(self) -> None:
        assert self._ws is not None
        while True:
            await asyncio.sleep(PING_INTERVAL_SECONDS)
            await self._ws.send(PING_TEXT)

    async def stream(self) -> AsyncIterator[dict[str, Any]]:
        """Yield parsed JSON messages until the connection closes.

        Flattens batched array frames into individual dicts. Non-JSON
        frames (e.g. the server's `PONG` reply) are silently skipped.
        """
        if self._ws is None:
            raise RuntimeError("PolymarketWebSocketClient must be used as an async context manager")
        async for raw in self._ws:
            text = raw if isinstance(raw, str) else raw.decode("utf-8")
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict):
                        yield cast(dict[str, Any], item)
            elif isinstance(parsed, dict):
                yield cast(dict[str, Any], parsed)
