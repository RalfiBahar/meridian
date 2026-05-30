"""Market UUID registry: lazy UPSERT-and-cache.

The canonical event already carries a deterministic `market_id` (uuid5 over
the venue ticker), so the registry's job is to make sure that UUID has a
matching row in `markets` before any ticks reference it (FK constraint).

Strategy:

- In-memory `set[UUID]` of UUIDs already verified present.
- On miss, INSERT ... ON CONFLICT DO NOTHING against `markets`. Either we
  inserted, or someone else already did — either way the row exists when
  the call returns.
- We use placeholder metadata (`question='(pending REST sync)'`,
  `category='unknown'`) and rely on a REST-driven backfill (Phase 1c.3
  or a later sub-phase) to enrich.
"""

from __future__ import annotations

from uuid import UUID

import asyncpg


class MarketRegistry:
    """Lazy UPSERT-and-cache of (venue, external_id) -> markets row."""

    def __init__(self, pool: asyncpg.Pool, *, venue_code: str = "kalshi") -> None:
        self._pool = pool
        self._venue_code = venue_code
        self._known: set[UUID] = set()
        self._venue_id: int | None = None
        self.new_market_count = 0

    async def _load_venue_id(self) -> int:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id FROM venues WHERE code = $1",
                self._venue_code,
            )
        if row is None:
            raise RuntimeError(f"venue {self._venue_code!r} not in `venues` table")
        venue_id = int(row["id"])
        self._venue_id = venue_id
        return venue_id

    async def ensure_market(self, market_id: UUID, external_id: str) -> bool:
        """Ensure the given market row exists. Returns True if newly inserted."""
        if market_id in self._known:
            return False
        venue_id = self._venue_id if self._venue_id is not None else await self._load_venue_id()
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                """
                INSERT INTO markets (id, venue_id, external_id, ticker, question, category)
                VALUES ($1, $2, $3, $3, '(pending REST sync)', 'unknown')
                ON CONFLICT (venue_id, external_id) DO NOTHING
                """,
                market_id,
                venue_id,
                external_id,
            )
        # Result is "INSERT 0 0" on conflict, "INSERT 0 1" when inserted.
        inserted = bool(result.endswith(" 1"))
        self._known.add(market_id)
        if inserted:
            self.new_market_count += 1
        return inserted
