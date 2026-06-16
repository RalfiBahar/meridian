"""CLI subcommands: `meridian markets {link-cross-venue}`.

Cross-venue pairing (e.g. a Kalshi Fed-rate contract and its Polymarket
equivalent) reuses the existing `market_groups` table with
`group_type='cross_venue'` rather than a dedicated mapping table — see
TASKS.md 1d-e and `markets.market_group_id` in `migrations/0001_initial_schema.sql`.
No schema change was needed for this.
"""

from __future__ import annotations

import asyncio
import sys
from uuid import UUID

import asyncpg
import click

from meridian.config import get_settings
from meridian.db.postgres import pool_context
from meridian.logging import configure_logging, get_logger


async def link_cross_venue(
    pool: asyncpg.Pool,
    market_id_a: UUID,
    market_id_b: UUID,
    *,
    label: str,
    description: str | None = None,
) -> tuple[UUID, int]:
    """Create a `cross_venue` market_group and assign both markets to it.

    Returns `(group_id, updated_count)`. `updated_count` should be 2; a
    lower count means one or both market IDs didn't match an existing row.
    """
    async with pool.acquire() as conn, conn.transaction():
        group_id = await conn.fetchval(
            """
            INSERT INTO market_groups (label, group_type, description)
            VALUES ($1, 'cross_venue', $2)
            RETURNING id
            """,
            label,
            description,
        )
        result = await conn.execute(
            "UPDATE markets SET market_group_id = $1, updated_at = now() WHERE id IN ($2, $3)",
            group_id,
            market_id_a,
            market_id_b,
        )
    updated_count = int(result.split()[-1])
    return UUID(str(group_id)), updated_count


@click.group(name="markets")
def markets() -> None:
    """Cross-venue market metadata utilities."""


@markets.command(name="link-cross-venue")
@click.argument("market_id_a", type=click.UUID)
@click.argument("market_id_b", type=click.UUID)
@click.option("--label", required=True, help="Human-readable label, e.g. the shared question.")
@click.option("--description", default=None, help="Optional longer description.")
def link_cross_venue_cmd(
    market_id_a: UUID,
    market_id_b: UUID,
    label: str,
    description: str | None,
) -> None:
    """Pair two markets.id rows (typically Kalshi + Polymarket) into one market_group."""
    sys.exit(asyncio.run(_link(market_id_a, market_id_b, label, description)))


async def _link(
    market_id_a: UUID,
    market_id_b: UUID,
    label: str,
    description: str | None,
) -> int:
    settings = get_settings()
    configure_logging(settings)
    log = get_logger("meridian.markets.cli")
    async with pool_context(settings) as pool:
        group_id, updated_count = await link_cross_venue(
            pool, market_id_a, market_id_b, label=label, description=description
        )
    if updated_count != 2:
        log.error(
            "markets.link_failed",
            group_id=str(group_id),
            updated_count=updated_count,
            market_a=str(market_id_a),
            market_b=str(market_id_b),
        )
        return 1
    log.info(
        "markets.linked",
        group_id=str(group_id),
        market_a=str(market_id_a),
        market_b=str(market_id_b),
        label=label,
    )
    return 0
