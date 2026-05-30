"""Orchestrate Kalshi WS -> normalize -> persist.

Phase 1c.2 scope: a single connection, fixed duration. Reconnect/backoff
and long-running operation arrive in 1c.3.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import asyncpg

from meridian.config import Settings
from meridian.events import CanonicalEvent
from meridian.ingest.gap import GapDetector
from meridian.ingest.registry import MarketRegistry
from meridian.ingest.stats import IngestStats
from meridian.ingest.writer import TickWriter
from meridian.kalshi.normalize import normalize_kalshi_message
from meridian.kalshi.ws import DEFAULT_CHANNELS, KalshiWebSocketClient
from meridian.logging import get_logger


class KalshiIngestWorker:
    def __init__(
        self,
        settings: Settings,
        pool: asyncpg.Pool,
        *,
        tickers: list[str],
        channels: tuple[str, ...] = DEFAULT_CHANNELS,
    ) -> None:
        self._settings = settings
        self._pool = pool
        self._tickers = tickers
        self._channels = channels
        self._registry = MarketRegistry(pool, venue_code="kalshi")
        self._writer = TickWriter(pool)
        self._gaps = GapDetector(pool)
        self._stats = IngestStats()
        self._log = get_logger("meridian.kalshi.ingest")

    async def run(self, *, seconds: int) -> IngestStats:
        deadline = time.monotonic() + seconds
        async with KalshiWebSocketClient(
            self._settings, tickers=self._tickers, channels=self._channels
        ) as client:
            stream = client.stream()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    raw = await asyncio.wait_for(stream.__anext__(), timeout=remaining)
                except (TimeoutError, StopAsyncIteration):
                    break
                await self._handle(raw)
        self._stats.new_markets = self._registry.new_market_count
        self._stats.gaps_detected = self._gaps.gaps_detected
        return self._stats

    async def _handle(self, raw: dict[str, Any]) -> None:
        self._stats.received += 1
        await self._gaps.observe(raw)
        event = normalize_kalshi_message(raw)
        if event is None:
            self._classify_skipped(raw)
            return
        await self._persist(event)

    def _classify_skipped(self, raw: dict[str, Any]) -> None:
        raw_type = raw.get("type")
        msg_type = raw_type if isinstance(raw_type, str) else "?"
        if msg_type in ("subscribed", "ok", "unsubscribed"):
            self._stats.control += 1
        else:
            self._stats.unknown_types[msg_type] = self._stats.unknown_types.get(msg_type, 0) + 1

    async def _persist(self, event: CanonicalEvent) -> None:
        self._stats.normalized += 1
        await self._registry.ensure_market(event.market_id, event.external_market_id)
        try:
            rows = await self._writer.write(event)
        except Exception as exc:
            self._log.error(
                "ingest.write_failed",
                ticker=event.external_market_id,
                kind=event.payload.kind.value,
                error=str(exc),
            )
            return
        self._stats.rows_written += rows
