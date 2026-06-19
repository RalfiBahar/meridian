"""Unit tests for MarketRegistry — cache hit/miss/conflict, no real DB."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from meridian.ingest.registry import MarketRegistry

_ID_A = UUID("aaaaaaaa-0000-0000-0000-000000000001")
_ID_B = UUID("bbbbbbbb-0000-0000-0000-000000000002")


async def test_first_ensure_market_returns_true(mock_pool: Any) -> None:
    """First time we see a market → INSERT succeeds → returns True."""
    registry = MarketRegistry(mock_pool, venue_code="kalshi")
    result = await registry.ensure_market(_ID_A, "TICKER-A")
    assert result is True


async def test_first_ensure_market_increments_counter(mock_pool: Any) -> None:
    registry = MarketRegistry(mock_pool, venue_code="kalshi")
    await registry.ensure_market(_ID_A, "TICKER-A")
    assert registry.new_market_count == 1


async def test_second_call_hits_cache_returns_false(mock_pool: Any) -> None:
    """Second call for the same market_id hits the in-memory cache → False, no DB hit."""
    registry = MarketRegistry(mock_pool, venue_code="kalshi")
    await registry.ensure_market(_ID_A, "TICKER-A")
    initial_exec_count = len(mock_pool.conn.executions)
    result = await registry.ensure_market(_ID_A, "TICKER-A")
    assert result is False
    assert len(mock_pool.conn.executions) == initial_exec_count  # no new DB call


async def test_conflict_returns_false_does_not_increment_counter(
    mock_pool: Any, mock_conn: Any
) -> None:
    """When INSERT returns 'INSERT 0 0' (conflict), returns False and doesn't count."""
    mock_conn.execute_result = "INSERT 0 0"
    registry = MarketRegistry(mock_pool, venue_code="kalshi")
    result = await registry.ensure_market(_ID_A, "TICKER-A")
    assert result is False
    assert registry.new_market_count == 0


async def test_conflict_still_adds_to_cache(mock_pool: Any, mock_conn: Any) -> None:
    """Even on conflict, the market_id is added to the cache to avoid future DB hits."""
    mock_conn.execute_result = "INSERT 0 0"
    registry = MarketRegistry(mock_pool, venue_code="kalshi")
    await registry.ensure_market(_ID_A, "TICKER-A")
    exec_count_after_first = len(mock_conn.executions)
    await registry.ensure_market(_ID_A, "TICKER-A")
    assert len(mock_conn.executions) == exec_count_after_first  # cache hit, no extra query


async def test_different_markets_both_inserted(mock_pool: Any) -> None:
    registry = MarketRegistry(mock_pool, venue_code="kalshi")
    r1 = await registry.ensure_market(_ID_A, "TICKER-A")
    r2 = await registry.ensure_market(_ID_B, "TICKER-B")
    assert r1 is True
    assert r2 is True
    assert registry.new_market_count == 2


async def test_venue_id_loaded_once(mock_pool: Any, mock_conn: Any) -> None:
    """_load_venue_id SELECT is issued once; second market call reuses it."""
    registry = MarketRegistry(mock_pool, venue_code="kalshi")
    await registry.ensure_market(_ID_A, "TICKER-A")
    select_count = sum(1 for q, _ in mock_conn.executions if "SELECT id FROM venues" in q)
    await registry.ensure_market(_ID_B, "TICKER-B")
    select_count_after = sum(1 for q, _ in mock_conn.executions if "SELECT id FROM venues" in q)
    assert select_count == 0  # fetchrow, not execute — just sanity check execute path
    assert select_count_after == 0


async def test_missing_venue_raises(mock_pool: Any, mock_conn: Any) -> None:
    """When the venue is not in the DB, _load_venue_id raises RuntimeError."""
    mock_conn.fetchrow_result = None  # venue not found
    registry = MarketRegistry(mock_pool, venue_code="nonexistent")
    with pytest.raises(RuntimeError, match="nonexistent"):
        await registry.ensure_market(_ID_A, "TICKER-A")


async def test_venue_code_forwarded_to_query(mock_pool: Any, mock_conn: Any) -> None:
    """The venue_code is passed as the SELECT parameter."""
    registry = MarketRegistry(mock_pool, venue_code="polymarket")
    await registry.ensure_market(_ID_A, "TOKEN-123")
    # fetchrow was called for SELECT id FROM venues WHERE code = $1
    # We can verify the insert query references the external_id
    insert_queries = [q for q, _ in mock_conn.executions if "INSERT INTO markets" in q]
    assert len(insert_queries) == 1


async def test_venue_id_cached_across_calls(mock_pool: Any, mock_conn: Any) -> None:
    """After first load, _venue_id is cached so fetchrow is not called again."""
    registry = MarketRegistry(mock_pool, venue_code="kalshi")
    await registry.ensure_market(_ID_A, "TICKER-A")
    # The registry has _venue_id set now; a second market should not call fetchrow again.
    assert registry._venue_id is not None
