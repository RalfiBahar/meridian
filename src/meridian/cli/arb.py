"""CLI subcommands: `meridian arb {monitor,group-fed}`."""

from __future__ import annotations

import asyncio
from decimal import Decimal

import click

from meridian.config import get_settings
from meridian.db.postgres import pool_context
from meridian.logging import configure_logging


@click.group(name="arb")
def arb() -> None:
    """No-arbitrage consistency and cross-venue divergence monitors."""


@arb.command(name="monitor")
@click.option(
    "--threshold-bps",
    default=5,
    show_default=True,
    type=int,
    help="Only report violations above this severity (basis points).",
)
@click.option(
    "--write-signals",
    is_flag=True,
    default=False,
    help="Persist detected violations to the signals table.",
)
@click.option(
    "--live",
    is_flag=True,
    default=False,
    help="Poll continuously every 30 seconds until interrupted.",
)
def monitor_cmd(threshold_bps: int, write_signals: bool, live: bool) -> None:
    """Scan partition groups and cross-venue pairs for no-arb violations.

    Prints a table of detected violations with severity in basis points.
    Use --write-signals to also persist them to the signals table for
    downstream dashboards / alerting.
    """
    asyncio.run(
        _run_monitor(
            threshold_bps=Decimal(str(threshold_bps)),
            write_signals=write_signals,
            live=live,
        )
    )


async def _run_monitor(
    *,
    threshold_bps: Decimal,
    write_signals: bool,
    live: bool,
) -> None:
    import asyncio as _asyncio

    from meridian.analytics.arb import run_cross_venue_monitor, run_partition_monitor

    settings = get_settings()
    configure_logging(settings)

    while True:
        async with pool_context(settings) as pool:
            partition_hits = await run_partition_monitor(
                pool, threshold_bps=threshold_bps, write_signals=write_signals
            )
            cross_hits = await run_cross_venue_monitor(
                pool, threshold_bps=threshold_bps, write_signals=write_signals
            )

        if not partition_hits and not cross_hits:
            click.echo(f"No violations above {threshold_bps} bps found.")
        else:
            if partition_hits:
                click.echo(f"\n{'─' * 72}")
                click.echo(f"{'PARTITION VIOLATIONS':^72}")
                click.echo(f"{'─' * 72}")
                click.echo(f"{'Group':<30} {'N':>3} {'Bps':>8} {'Dir':<6} {'Depth':<8}")
                click.echo("─" * 60)
                for r in partition_hits:
                    click.echo(
                        f"{r.group_label[:29]:<30} {r.n_contracts:>3} "
                        f"{float(r.violation_bps):>8.1f} {r.direction:<6} "
                        f"{'YES' if r.depth_feasible else 'NO':<8}"
                    )

            if cross_hits:
                click.echo(f"\n{'─' * 72}")
                click.echo(f"{'CROSS-VENUE DIVERGENCES':^72}")
                click.echo(f"{'─' * 72}")
                click.echo(f"{'Group':<36} {'Divergence bps':>16}")
                click.echo("─" * 54)
                for cv in cross_hits:
                    cv_label = f"{cv.venue_a}↔{cv.venue_b} ({cv.group_id})"
                    click.echo(f"{cv_label[:35]:<36} {float(cv.divergence_bps):>16.1f}")

        if not live:
            break

        await _asyncio.sleep(30)


@arb.command(name="group-fed")
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Print proposed groups without writing to DB.",
)
def group_fed_cmd(dry_run: bool) -> None:
    """Group Kalshi FED-rate contracts into partitions by FOMC meeting date.

    Kalshi Fed-rate tickers follow the pattern KXFED-YYMM-T<rate>.
    All contracts with the same date component form a partition
    (mutually exclusive outcomes for one FOMC meeting).

    Creates or updates `market_groups` rows with group_type='partition'.
    Does not affect markets already in a group.
    """
    asyncio.run(_run_group_fed(dry_run=dry_run))


async def _run_group_fed(*, dry_run: bool) -> None:

    settings = get_settings()
    configure_logging(settings)

    async with pool_context(settings) as pool:
        # Find all open Kalshi markets matching KXFED-* pattern.
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT m.id, m.external_id
                FROM markets m
                JOIN venues v ON v.id = m.venue_id
                WHERE v.code = 'kalshi'
                  AND m.resolution_status = 'open'
                  AND m.external_id LIKE 'KXFED-%'
                  AND m.market_group_id IS NULL
                """
            )

        if not rows:
            click.echo("No ungrouped KXFED-* markets found.")
            return

        # Group by date component: 'KXFED-26JUN-T3.75' → '26JUN'.
        from collections import defaultdict

        by_date: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for r in rows:
            parts = r["external_id"].split("-")
            if len(parts) >= 2:
                date_key = parts[1]  # e.g. '26JUN'
                by_date[date_key].append((str(r["id"]), r["external_id"]))

        for date_key, markets in sorted(by_date.items()):
            label = f"KXFED partition {date_key}"
            click.echo(f"\n{label}  ({len(markets)} contracts)")
            for _mid, ticker in markets:
                click.echo(f"  {ticker}")

            if dry_run:
                continue

            async with pool.acquire() as conn:
                group_id = await conn.fetchval(
                    """
                    INSERT INTO market_groups (label, group_type, description)
                    VALUES ($1, 'partition', $2)
                    ON CONFLICT DO NOTHING
                    RETURNING id
                    """,
                    label,
                    f"Kalshi Fed-rate partition for FOMC date {date_key}",
                )
                if group_id is None:
                    group_id = await conn.fetchval(
                        "SELECT id FROM market_groups WHERE label = $1", label
                    )
                for market_id, _ in markets:
                    await conn.execute(
                        "UPDATE markets SET market_group_id = $1 WHERE id = $2",
                        group_id,
                        market_id,
                    )

        if not dry_run:
            click.echo("\nPartition groups created/updated.")
