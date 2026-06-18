"""Orchestrate Kalshi WS -> normalize -> persist.

Phase 1c.3: indefinite operation with exponential-backoff reconnect, Redis
Streams publishing, and background REST enrichment for new markets.
"""

from __future__ import annotations

import asyncio
import contextlib
import random
from typing import Any
from uuid import UUID

import asyncpg
import redis.asyncio as aioredis
from websockets.exceptions import ConnectionClosedOK

from meridian.config import Settings
from meridian.events import CanonicalEvent
from meridian.ingest.gap import GapDetector
from meridian.ingest.registry import MarketRegistry
from meridian.ingest.stats import IngestStats
from meridian.ingest.writer import TickWriter
from meridian.kalshi.normalize import normalize_kalshi_message
from meridian.kalshi.ws import DEFAULT_CHANNELS, KalshiWebSocketClient
from meridian.logging import get_logger
from meridian.metrics import (
    ingest_events_total,
    ingest_gaps_total,
    ingest_lag_seconds,
    ingest_reconnects_total,
)

_BACKOFF_INITIAL = 1.0
_BACKOFF_MAX = 60.0
_BACKOFF_JITTER = 0.20


async def _wait_for_event(event: asyncio.Event) -> None:
    await event.wait()


class KalshiIngestWorker:
    def __init__(
        self,
        settings: Settings,
        pool: asyncpg.Pool,
        *,
        tickers: list[str],
        channels: tuple[str, ...] = DEFAULT_CHANNELS,
        redis: aioredis.Redis | None = None,
    ) -> None:
        self._settings = settings
        self._pool = pool
        self._tickers = tickers
        self._channels = channels
        self._redis = redis
        self._registry = MarketRegistry(pool, venue_code="kalshi")
        self._writer = TickWriter(pool)
        self._gaps = GapDetector(pool)
        self._stats = IngestStats()
        self._log = get_logger("meridian.kalshi.ingest")
        self._bg_tasks: set[asyncio.Task[None]] = set()

    async def run(self, *, stop_event: asyncio.Event | None = None) -> IngestStats:
        """Run indefinitely, reconnecting with exponential backoff on error.

        Returns when `stop_event` is set (or the task is cancelled). On a
        clean WS close from the server, reconnects immediately and resets
        the backoff delay.
        """
        delay = _BACKOFF_INITIAL
        while True:
            if stop_event is not None and stop_event.is_set():
                break
            try:
                await self._run_connection(stop_event)
            except asyncio.CancelledError:
                raise
            except ConnectionClosedOK:
                # Server closed cleanly (WS code 1000) before we could
                # complete the subscribe or read messages — reconnect at once.
                if stop_event is not None and stop_event.is_set():
                    break
                self._stats.reconnects += 1
                ingest_reconnects_total.labels(venue="kalshi").inc()
                delay = _BACKOFF_INITIAL
                continue
            except Exception as exc:
                self._log.warning(
                    "ingest.reconnecting",
                    error=str(exc),
                    delay=round(delay, 2),
                )
                jitter = delay * random.uniform(-_BACKOFF_JITTER, _BACKOFF_JITTER)
                await asyncio.sleep(delay + jitter)
                delay = min(delay * 2, _BACKOFF_MAX)
                self._stats.reconnects += 1
                ingest_reconnects_total.labels(venue="kalshi").inc()
                continue

            # _run_connection returned normally: either stop_event fired or the
            # server closed the connection cleanly.
            if stop_event is not None and stop_event.is_set():
                break
            # Server-initiated close → reconnect immediately, reset backoff.
            self._stats.reconnects += 1
            ingest_reconnects_total.labels(venue="kalshi").inc()
            delay = _BACKOFF_INITIAL

        pending = list(self._bg_tasks)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        self._stats.new_markets = self._registry.new_market_count
        self._stats.gaps_detected = self._gaps.gaps_detected
        return self._stats

    async def _run_connection(self, stop_event: asyncio.Event | None) -> None:
        self._log.info(
            "ingest.connecting",
            tickers=len(self._tickers),
            channels=list(self._channels),
        )
        async with KalshiWebSocketClient(
            self._settings, tickers=self._tickers, channels=self._channels
        ) as client:
            drain: asyncio.Task[None] = asyncio.create_task(self._drain_stream(client))

            if stop_event is None:
                try:
                    await drain
                except asyncio.CancelledError:
                    drain.cancel()
                    raise
                return

            stop: asyncio.Task[None] = asyncio.create_task(_wait_for_event(stop_event))
            try:
                await asyncio.wait({drain, stop}, return_when=asyncio.FIRST_COMPLETED)
            except asyncio.CancelledError:
                drain.cancel()
                stop.cancel()
                raise

            for task in (drain, stop):
                if not task.done():
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task

            if drain.done() and not drain.cancelled():
                exc = drain.exception()
                if exc is not None:
                    raise exc

    async def _drain_stream(self, client: KalshiWebSocketClient) -> None:
        try:
            async for raw in client.stream():
                await self._handle(raw)
        except ConnectionClosedOK:
            pass  # server closed cleanly; run() will reconnect

    async def _handle(self, raw: dict[str, Any]) -> None:
        self._stats.received += 1
        gap = await self._gaps.observe(raw)
        if gap > 0:
            ingest_gaps_total.labels(venue="kalshi").inc()
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
        is_new = await self._registry.ensure_market(event.market_id, event.external_market_id)
        if is_new:
            task: asyncio.Task[None] = asyncio.create_task(
                self._enrich_market(event.external_market_id, event.market_id)
            )
            self._bg_tasks.add(task)
            task.add_done_callback(self._bg_tasks.discard)
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
        ingest_events_total.labels(venue="kalshi", kind=event.payload.kind.value).inc()
        lag = (event.ingest_ts - event.event_ts).total_seconds()
        ingest_lag_seconds.labels(venue="kalshi").observe(lag)
        if self._redis is not None:
            await self._publish(event)

    async def _publish(self, event: CanonicalEvent) -> None:
        assert self._redis is not None
        try:
            await self._redis.xadd("kalshi.events", {"event": event.model_dump_json()})
            self._stats.events_published += 1
        except Exception as exc:
            self._log.warning("ingest.publish_failed", error=str(exc))

    async def _enrich_market(self, ticker: str, market_id: UUID) -> None:
        from meridian.kalshi.client import KalshiClient

        try:
            async with KalshiClient(self._settings) as client:
                market = await client.get_market(ticker)
        except Exception as exc:
            self._log.warning("ingest.enrich_failed", ticker=ticker, error=str(exc))
            return

        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE markets
                SET question   = $1,
                    category   = $2,
                    opens_at   = $3,
                    closes_at  = $4,
                    updated_at = now()
                WHERE id = $5
                  AND question = '(pending REST sync)'
                """,
                market.title or ticker,
                market.category or "unknown",
                market.open_time,
                market.close_time,
                market_id,
            )
        self._log.info("ingest.market_enriched", ticker=ticker)
