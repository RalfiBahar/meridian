"""Integration tests for the migration runner against the live stack."""

from __future__ import annotations

from pathlib import Path

import asyncpg
import pytest

from meridian.config import Settings
from meridian.db.migrate import MigrationError, apply_pending, discover
from meridian.db.postgres import pool_context

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


def test_discover_finds_initial_schema() -> None:
    migrations = discover(MIGRATIONS)
    assert len(migrations) >= 1
    assert migrations[0].version == "0001_initial_schema"


@pytest.mark.integration
async def test_apply_pending_creates_and_seeds_schema() -> None:
    settings = Settings()
    async with pool_context(settings) as pool, pool.acquire() as conn:
        await _reset_schema(conn)

    applied = await apply_pending(settings, MIGRATIONS)
    assert "0001_initial_schema" in applied

    async with pool_context(settings) as pool, pool.acquire() as conn:
        venue_codes = {row["code"] for row in await conn.fetch("SELECT code FROM venues")}
        assert {"kalshi", "polymarket"}.issubset(venue_codes)
        is_hyper = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM timescaledb_information.hypertables "
            "WHERE hypertable_name = 'ticks')"
        )
        assert is_hyper is True


@pytest.mark.integration
async def test_apply_pending_is_idempotent() -> None:
    settings = Settings()
    await apply_pending(settings, MIGRATIONS)
    second_run = await apply_pending(settings, MIGRATIONS)
    assert second_run == []


@pytest.mark.integration
async def test_checksum_mismatch_raises(tmp_path: Path) -> None:
    settings = Settings()

    # Apply against a freshly-reset schema using the real migration.
    async with pool_context(settings) as pool, pool.acquire() as conn:
        await _reset_schema(conn)
    await apply_pending(settings, MIGRATIONS)

    # Now build a fake migrations dir where the same version has different content.
    real = MIGRATIONS / "0001_initial_schema.sql"
    fake_dir = tmp_path / "migrations"
    fake_dir.mkdir()
    (fake_dir / "0001_initial_schema.sql").write_text(real.read_text() + "\n-- tampered\n")

    with pytest.raises(MigrationError, match="edited after being applied"):
        await apply_pending(settings, fake_dir)


async def _reset_schema(conn: asyncpg.Connection) -> None:
    """Drop everything Meridian-created so a fresh migrate run can proceed.

    Used only by integration tests that need a clean slate.
    """
    await conn.execute("DROP TABLE IF EXISTS signals CASCADE")
    await conn.execute("DROP TABLE IF EXISTS book_snapshots CASCADE")
    await conn.execute("DROP TABLE IF EXISTS ticks CASCADE")
    await conn.execute("DROP TABLE IF EXISTS markets CASCADE")
    await conn.execute("DROP TABLE IF EXISTS market_groups CASCADE")
    await conn.execute("DROP TABLE IF EXISTS news_events CASCADE")
    await conn.execute("DROP TABLE IF EXISTS venues CASCADE")
    await conn.execute("DROP TABLE IF EXISTS schema_migrations CASCADE")
