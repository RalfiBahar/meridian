"""CLI subcommands: `meridian markets {discover,link-cross-venue,sync-settled}`."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from uuid import UUID

import asyncpg
import click

from meridian.config import get_settings
from meridian.db.postgres import pool_context
from meridian.ingest.market_discovery import discover_ingest_markets, update_dotenv_keys, write_ingest_env
from meridian.ingest.settled_backfill import SERIES_BY_CATEGORY, sync_settled_markets
from meridian.kalshi.client import KalshiClient
from meridian.kalshi.errors import KalshiAuthError
from meridian.logging import configure_logging, get_logger


async def link_cross_venue(
    pool: asyncpg.Pool,
    market_id_a: UUID,
    market_id_b: UUID,
    *,
    label: str,
    description: str | None = None,
) -> tuple[UUID, int]:
    """Create a `cross_venue` market_group and assign both markets to it."""
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
    """Market metadata and ingest discovery utilities."""


@markets.command(name="discover")
@click.option(
    "--write-env",
    default="config/ingest.env",
    show_default=True,
    help="Write discovered tickers to this dotenv file.",
)
@click.option(
    "--update-dotenv",
    is_flag=True,
    default=True,
    help="Also merge KALSHI_INGEST_TICKERS into .env.",
)
def discover_cmd(write_env: str, update_dotenv: bool) -> None:
    """Discover high-volume Kalshi + Polymarket markets for live ingest."""
    sys.exit(asyncio.run(_discover(write_env, update_dotenv)))


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


@markets.command(name="sync-settled")
@click.option(
    "--category",
    "categories",
    multiple=True,
    help="Limit to category tab(s): fed, econ, politics, crypto, sports.",
)
@click.option("--max-per-series", default=100, show_default=True, type=int)
@click.option(
    "--period-interval",
    default=1440,
    show_default=True,
    type=click.Choice(["1", "60", "1440"]),
    help="Candlestick interval in minutes (1440 = daily).",
)
@click.option(
    "--keep-synthetic",
    is_flag=True,
    help="Do not delete synthetic demo calibration rows.",
)
def sync_settled_cmd(
    categories: tuple[str, ...],
    max_per_series: int,
    period_interval: str,
    keep_synthetic: bool,
) -> None:
    """Fetch settled Kalshi markets + candlestick p_mid history for /calibration."""
    sys.exit(
        asyncio.run(
            _sync_settled(
                list(categories) or None,
                max_per_series=max_per_series,
                period_interval=int(period_interval),
                remove_synthetic=not keep_synthetic,
            )
        )
    )


async def _discover(write_env: str, update_dotenv: bool) -> int:
    settings = get_settings()
    configure_logging(settings)
    log = get_logger("meridian.markets.cli")

    client: KalshiClient | None = None
    try:
        client = KalshiClient(settings)
        await client.__aenter__()
    except KalshiAuthError as exc:
        log.warning("markets.discover.kalshi_auth", error=str(exc))
        client = None

    try:
        discovery = await discover_ingest_markets(client)
    finally:
        if client is not None:
            await client.__aexit__(None, None, None)

    root = Path(__file__).resolve().parents[3]
    env_path = root / write_env
    env_path.parent.mkdir(parents=True, exist_ok=True)
    write_ingest_env(str(env_path), discovery)
    if update_dotenv:
        update_dotenv_keys(str(root / ".env"), discovery)

    click.echo(discovery.summary())
    click.echo(f"Wrote {env_path}")
    return 0


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


async def _sync_settled(
    categories: list[str] | None,
    *,
    max_per_series: int,
    period_interval: int,
    remove_synthetic: bool,
) -> int:
    settings = get_settings()
    configure_logging(settings)
    log = get_logger("meridian.markets.cli")

    if categories:
        unknown = [c for c in categories if c not in SERIES_BY_CATEGORY]
        if unknown:
            click.echo(f"error: unknown categories: {', '.join(unknown)}", err=True)
            return 2

    async with pool_context(settings) as pool, KalshiClient(settings) as client:
        result = await sync_settled_markets(
            pool,
            client,
            categories=categories,
            max_per_series=max_per_series,
            period_interval=period_interval,
            remove_synthetic=remove_synthetic,
        )

    click.echo(result.summary())
    if result.errors:
        for err in result.errors[:10]:
            log.warning("markets.sync_settled.partial_error", error=err)

    if result.markets_upserted == 0:
        return 1
    return 0
