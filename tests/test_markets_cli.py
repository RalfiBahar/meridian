"""Unit tests for `meridian markets link-cross-venue`'s core logic."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID, uuid4

from meridian.cli.markets import link_cross_venue

_GROUP_ID = uuid4()


class _MockConn:
    def __init__(self, *, updated_count: int) -> None:
        self._updated_count = updated_count
        self.executed: list[tuple[str, tuple[object, ...]]] = []

    def transaction(self) -> Any:
        @asynccontextmanager
        async def _txn() -> Any:
            yield

        return _txn()

    async def fetchval(self, query: str, *args: object) -> object:
        self.executed.append((query, args))
        return _GROUP_ID

    async def execute(self, query: str, *args: object) -> str:
        self.executed.append((query, args))
        return f"UPDATE 0 {self._updated_count}"


class _MockPool:
    def __init__(self, *, updated_count: int = 2) -> None:
        self.conn = _MockConn(updated_count=updated_count)

    @asynccontextmanager
    async def acquire(self) -> Any:
        yield self.conn


async def test_link_cross_venue_creates_group_and_updates_both_markets() -> None:
    pool = _MockPool(updated_count=2)
    market_a, market_b = uuid4(), uuid4()

    group_id, updated_count = await link_cross_venue(
        pool,
        market_a,
        market_b,
        label="Fed funds >= 4.00% — July FOMC",
    )

    assert group_id == UUID(str(_GROUP_ID))
    assert updated_count == 2

    insert_sql, insert_args = pool.conn.executed[0]
    assert "INSERT INTO market_groups" in insert_sql
    assert "cross_venue" in insert_sql
    assert insert_args[0] == "Fed funds >= 4.00% — July FOMC"

    update_sql, update_args = pool.conn.executed[1]
    assert "UPDATE markets" in update_sql
    assert update_args == (_GROUP_ID, market_a, market_b)


async def test_link_cross_venue_reports_partial_update() -> None:
    """If only one market_id matched, updated_count reflects that."""
    pool = _MockPool(updated_count=1)

    _, updated_count = await link_cross_venue(
        pool,
        uuid4(),
        uuid4(),
        label="mismatched ids",
    )

    assert updated_count == 1
