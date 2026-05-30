"""Idempotent tick writer.

Routes a CanonicalEvent to the right hypertable:

- BookEvent       -> book_snapshots, one row per level
- BookDeltaEvent  -> ticks (kind='book_delta', payload jsonb)
- QuoteEvent      -> ticks (kind='quote',      bid/ask/sizes columns)
- TradeEvent      -> ticks (kind='trade',      trade_price/size/aggressor)
- StatusEvent     -> ticks (kind='status',     payload jsonb)

Idempotency: every INSERT uses ON CONFLICT DO NOTHING against the table's
primary key, so reconnect-and-replay is safe at the database boundary.
"""

from __future__ import annotations

import json

import asyncpg

from meridian.events import (
    BookDeltaEvent,
    BookEvent,
    CanonicalEvent,
    QuoteEvent,
    StatusEvent,
    TradeEvent,
)


class TickWriter:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def write(self, event: CanonicalEvent) -> int:
        """Persist one event. Returns the number of rows actually inserted."""
        payload = event.payload
        if isinstance(payload, BookEvent):
            return await self._write_book_snapshot(event, payload)
        if isinstance(payload, BookDeltaEvent):
            return await self._write_book_delta(event, payload)
        if isinstance(payload, QuoteEvent):
            return await self._write_quote(event, payload)
        if isinstance(payload, TradeEvent):
            return await self._write_trade(event, payload)
        if isinstance(payload, StatusEvent):
            return await self._write_status(event, payload)
        raise TypeError(f"unhandled payload type: {type(payload).__name__}")

    async def _write_book_snapshot(self, event: CanonicalEvent, snap: BookEvent) -> int:
        if not snap.levels:
            return 0
        records = [
            (
                event.event_ts,
                event.market_id,
                event.sequence_no,
                lv.side,
                lv.level,
                lv.price,
                lv.size,
            )
            for lv in snap.levels
        ]
        async with self._pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO book_snapshots
                  (event_ts, market_id, sequence_no, side, level, price, size)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT DO NOTHING
                """,
                records,
            )
        # executemany doesn't return per-row insert counts; report rows attempted.
        return len(records)

    async def _write_book_delta(self, event: CanonicalEvent, delta: BookDeltaEvent) -> int:
        payload_json = json.dumps(
            {
                "side": delta.side,
                "price": str(delta.price),
                "delta": str(delta.delta),
            }
        )
        return await self._insert_tick(
            event,
            kind="book_delta",
            payload_json=payload_json,
        )

    async def _write_quote(self, event: CanonicalEvent, q: QuoteEvent) -> int:
        return await self._insert_tick(
            event,
            kind="quote",
            bid=q.bid,
            ask=q.ask,
            bid_size=q.bid_size,
            ask_size=q.ask_size,
        )

    async def _write_trade(self, event: CanonicalEvent, t: TradeEvent) -> int:
        return await self._insert_tick(
            event,
            kind="trade",
            trade_price=t.price,
            trade_size=t.size,
            aggressor=t.aggressor,
        )

    async def _write_status(self, event: CanonicalEvent, s: StatusEvent) -> int:
        return await self._insert_tick(
            event,
            kind="status",
            payload_json=json.dumps({"status": s.status}),
        )

    async def _insert_tick(
        self,
        event: CanonicalEvent,
        *,
        kind: str,
        bid: object | None = None,
        ask: object | None = None,
        bid_size: object | None = None,
        ask_size: object | None = None,
        trade_price: object | None = None,
        trade_size: object | None = None,
        aggressor: str | None = None,
        payload_json: str = "{}",
    ) -> int:
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                """
                INSERT INTO ticks
                  (event_ts, market_id, sequence_no, kind,
                   bid, ask, bid_size, ask_size,
                   trade_price, trade_size, aggressor,
                   payload, ingest_ts)
                VALUES ($1, $2, $3, $4,
                        $5, $6, $7, $8,
                        $9, $10, $11,
                        $12::jsonb, $13)
                ON CONFLICT DO NOTHING
                """,
                event.event_ts,
                event.market_id,
                event.sequence_no,
                kind,
                bid,
                ask,
                bid_size,
                ask_size,
                trade_price,
                trade_size,
                aggressor,
                payload_json,
                event.ingest_ts,
            )
        return 1 if result.endswith(" 1") else 0
