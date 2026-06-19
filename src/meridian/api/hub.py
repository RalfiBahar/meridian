"""WebSocket event broadcast hub (Phase 7).

Reads CanonicalEvent JSON from Redis Streams (kalshi.events, polymarket.events)
and fans out to connected WebSocket subscribers by channel:

  "all"             — every event from every stream
  "market:<uuid>"   — events for one specific market_id
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections import defaultdict
from typing import Any

_log = logging.getLogger(__name__)

_STREAMS = ["kalshi.events", "polymarket.events"]
_BLOCK_MS = 500


class EventHub:
    """Pub-sub hub between Redis Streams and WebSocket clients."""

    def __init__(self) -> None:
        self._queues: dict[str, list[asyncio.Queue[dict[str, Any]]]] = defaultdict(list)

    async def run(self, redis: Any) -> None:
        """Drain Redis streams and broadcast to subscribers until cancelled."""
        cursors: dict[str, str] = dict.fromkeys(_STREAMS, "$")
        while True:
            try:
                results: list[Any] = await redis.xread(streams=cursors, block=_BLOCK_MS, count=50)
                for stream_name, messages in results or []:
                    for msg_id, fields in messages:
                        raw: str | None = fields.get("event")
                        if raw:
                            try:
                                event: dict[str, Any] = json.loads(raw)
                            except json.JSONDecodeError:
                                continue
                            market_id: str | None = event.get("market_id")
                            await self._broadcast("all", event)
                            if market_id:
                                await self._broadcast(f"market:{market_id}", event)
                        cursors[stream_name] = msg_id
            except asyncio.CancelledError:
                return
            except Exception as exc:
                _log.warning("hub.redis_error: %s", exc)
                await asyncio.sleep(1)

    async def _broadcast(self, channel: str, event: dict[str, Any]) -> None:
        for q in list(self._queues.get(channel, [])):
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(event)

    def subscribe(self, channel: str) -> asyncio.Queue[dict[str, Any]]:
        """Register a new subscriber queue for *channel*."""
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=200)
        self._queues[channel].append(q)
        return q

    def unsubscribe(self, channel: str, q: asyncio.Queue[dict[str, Any]]) -> None:
        """Remove a subscriber queue."""
        with contextlib.suppress(KeyError, ValueError):
            self._queues[channel].remove(q)
