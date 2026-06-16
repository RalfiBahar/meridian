"""Orchestrate Polymarket WS -> normalize -> persist.

Mirrors `ingest/worker.py`'s shape, built on the shared
`ingest.reconnect.run_with_reconnect` state machine. Two differences are
forced by the protocol itself (see `docs/polymarket.md`):

- No `GapDetector`: Polymarket's market channel carries no sequence number
  of any kind, so there is nothing to detect a gap against.
- `normalize_polymarket_message()` returns a *list* of events (a batched
  `price_change` message can update several book levels at once) and needs
  a caller-owned `PolymarketBookState` to reconstruct signed deltas from
  Polymarket's absolute-size wire format (ADR-016).
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import asyncpg
import redis.asyncio as aioredis

from meridian.config import Settings
from meridian.events import CanonicalEvent
from meridian.ingest.reconnect import run_with_reconnect
from meridian.ingest.registry import MarketRegistry
from meridian.ingest.stats import IngestStats
from meridian.ingest.writer import TickWriter
from meridian.logging import get_logger
from meridian.polymarket.normalize import PolymarketBookState, normalize_polymarket_message
from meridian.polymarket.ws import PolymarketWebSocketClient

_INFORMATIONAL_TYPES = ("new_market", "market_resolved", "tick_size_change", "best_bid_ask")


class PolymarketIngestWorker:
    def __init__(
        self,
        settings: Settings,
        pool: asyncpg.Pool,
        *,
        asset_ids: list[str],
        redis: aioredis.Redis | None = None,
    ) -> None:
        self._settings = settings
        self._pool = pool
        self._asset_ids = asset_ids
        self._redis = redis
        self._registry = MarketRegistry(pool, venue_code="polymarket")
        self._writer = TickWriter(pool)
        self._book_state = PolymarketBookState()
        self._stats = IngestStats()
        self._log = get_logger("meridian.polymarket.ingest")
        self._bg_tasks: set[asyncio.Task[None]] = set()

    async def run(self, *, stop_event: asyncio.Event | None = None) -> IngestStats:
        """Run indefinitely, reconnecting with exponential backoff on error.

        Returns when `stop_event` is set (or the task is cancelled).
        """
        await run_with_reconnect(
            connect=self._connect,
            handle_one=self._handle,
            stats=self._stats,
            log=self._log,
            stop_event=stop_event,
        )
        pending = list(self._bg_tasks)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._stats.new_markets = self._registry.new_market_count
        return self._stats

    def _connect(self) -> PolymarketWebSocketClient:
        self._log.info("ingest.connecting", assets=len(self._asset_ids))
        return PolymarketWebSocketClient(asset_ids=self._asset_ids)

    async def _handle(self, raw: dict[str, Any]) -> None:
        self._stats.received += 1
        events = normalize_polymarket_message(raw, book_state=self._book_state)
        if not events:
            self._classify_skipped(raw)
            return
        for event in events:
            await self._persist(event, raw)

    def _classify_skipped(self, raw: dict[str, Any]) -> None:
        event_type = raw.get("event_type")
        msg_type = event_type if isinstance(event_type, str) else "?"
        if msg_type in _INFORMATIONAL_TYPES:
            self._stats.control += 1
        else:
            self._stats.unknown_types[msg_type] = self._stats.unknown_types.get(msg_type, 0) + 1

    async def _persist(self, event: CanonicalEvent, raw: dict[str, Any]) -> None:
        self._stats.normalized += 1
        is_new = await self._registry.ensure_market(event.market_id, event.external_market_id)
        if is_new:
            condition_id = raw.get("market")
            if isinstance(condition_id, str) and condition_id:
                task: asyncio.Task[None] = asyncio.create_task(
                    self._enrich_market(event.external_market_id, event.market_id, condition_id)
                )
                self._bg_tasks.add(task)
                task.add_done_callback(self._bg_tasks.discard)
        try:
            rows = await self._writer.write(event)
        except Exception as exc:
            self._log.error(
                "ingest.write_failed",
                asset_id=event.external_market_id,
                kind=event.payload.kind.value,
                error=str(exc),
            )
            return
        self._stats.rows_written += rows
        if self._redis is not None:
            await self._publish(event)

    async def _publish(self, event: CanonicalEvent) -> None:
        assert self._redis is not None
        try:
            await self._redis.xadd("polymarket.events", {"event": event.model_dump_json()})
            self._stats.events_published += 1
        except Exception as exc:
            self._log.warning("ingest.publish_failed", error=str(exc))

    async def _enrich_market(self, asset_id: str, market_id: UUID, condition_id: str) -> None:
        from meridian.polymarket.client import PolymarketClient

        try:
            async with PolymarketClient(self._settings) as client:
                market = await client.get_market(condition_id)
        except Exception as exc:
            self._log.warning("ingest.enrich_failed", condition_id=condition_id, error=str(exc))
            return

        outcome = next((t.outcome for t in market.tokens if t.token_id == asset_id), None)
        question = f"{market.question} — {outcome}" if outcome else market.question

        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE markets
                SET question   = $1,
                    category   = $2,
                    closes_at  = $3,
                    updated_at = now()
                WHERE id = $4
                  AND question = '(pending REST sync)'
                """,
                question,
                "unknown",
                market.end_date_iso,
                market_id,
            )
        self._log.info("ingest.market_enriched", asset_id=asset_id, condition_id=condition_id)
