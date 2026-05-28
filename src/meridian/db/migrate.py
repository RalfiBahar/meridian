"""Forward-only SQL migration runner.

Numbered .sql files in a migrations directory are applied in lexicographic
order. Each migration runs inside its own connection. Applied versions are
recorded in `schema_migrations` along with a SHA-256 checksum so we can
detect after-the-fact edits.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import asyncpg

from meridian.config import Settings
from meridian.db.postgres import pool_context

SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    checksum   TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


@dataclass(frozen=True)
class Migration:
    version: str
    path: Path
    checksum: str


class MigrationError(RuntimeError):
    """Raised when a migration cannot be applied safely."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def discover(migrations_dir: Path) -> list[Migration]:
    """Find forward migration files, sorted by version."""
    if not migrations_dir.is_dir():
        raise MigrationError(f"migrations directory not found: {migrations_dir}")
    migrations: list[Migration] = []
    for path in sorted(migrations_dir.glob("*.sql")):
        body = path.read_bytes()
        migrations.append(Migration(version=path.stem, path=path, checksum=_sha256(body)))
    return migrations


async def _ensure_table(conn: asyncpg.Connection) -> None:
    await conn.execute(SCHEMA_MIGRATIONS_DDL)


async def _applied(conn: asyncpg.Connection) -> dict[str, str]:
    rows = await conn.fetch("SELECT version, checksum FROM schema_migrations")
    return {row["version"]: row["checksum"] for row in rows}


async def _apply_one(conn: asyncpg.Connection, migration: Migration) -> None:
    sql = migration.path.read_text(encoding="utf-8")
    # Each migration is responsible for its own BEGIN/COMMIT; we run it as-is
    # so multi-statement scripts (including SELECT create_hypertable) work.
    await conn.execute(sql)
    await conn.execute(
        "INSERT INTO schema_migrations(version, checksum) VALUES ($1, $2)",
        migration.version,
        migration.checksum,
    )


async def apply_pending(settings: Settings, migrations_dir: Path) -> list[str]:
    """Apply every pending migration. Returns the list of versions applied."""
    discovered = discover(migrations_dir)
    applied_versions: list[str] = []

    async with pool_context(settings) as pool, pool.acquire() as conn:
        await _ensure_table(conn)
        existing = await _applied(conn)

        for migration in discovered:
            recorded_checksum = existing.get(migration.version)
            if recorded_checksum is not None:
                if recorded_checksum != migration.checksum:
                    raise MigrationError(
                        f"migration {migration.version!r} was edited after being "
                        f"applied (recorded={recorded_checksum[:12]}, "
                        f"file={migration.checksum[:12]})"
                    )
                continue
            await _apply_one(conn, migration)
            applied_versions.append(migration.version)

    return applied_versions
