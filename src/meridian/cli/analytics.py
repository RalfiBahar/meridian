"""CLI subcommands: `meridian analytics {calibrate,signals}`."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import click

from meridian.config import get_settings
from meridian.db.postgres import pool_context
from meridian.logging import configure_logging


@click.group(name="analytics")
def analytics() -> None:
    """Analytics and calibration commands."""


@analytics.command(name="calibrate")
@click.option(
    "--category",
    default=None,
    help="Filter to markets with this category (e.g. 'fed'). Omit for all.",
)
@click.option(
    "--lookback",
    default=None,
    type=int,
    metavar="DAYS",
    help="Only include p_mid signals from the last N days. Omit for all history.",
)
@click.option(
    "--bins",
    default=10,
    show_default=True,
    help="Number of reliability-diagram bins.",
)
@click.option(
    "--write-signals",
    is_flag=True,
    default=False,
    help="Persist calibration results to the signals table.",
)
def calibrate_cmd(
    category: str | None,
    lookback: int | None,
    bins: int,
    write_signals: bool,
) -> None:
    """Compute Brier score, log loss, and reliability diagram for resolved markets.

    Reads historical p_mid signals (written by `meridian analytics signals`)
    and compares them to each market's settled_value.

    Optionally writes calibration_brier / calibration_log_loss /
    calibration_reliability rows to the signals table.
    """
    lb = timedelta(days=lookback) if lookback is not None else None
    asyncio.run(
        _run_calibrate(category=category, lookback=lb, n_bins=bins, write_signals=write_signals)
    )


async def _run_calibrate(
    *,
    category: str | None,
    lookback: timedelta | None,
    n_bins: int,
    write_signals: bool,
) -> None:
    from meridian.analytics.calibration import run_calibration, write_calibration_signals

    settings = get_settings()
    configure_logging(settings)

    async with pool_context(settings) as pool:
        result = await run_calibration(pool, category=category, lookback=lookback, n_bins=n_bins)

    if result is None:
        click.echo("No resolved markets with p_mid signals found for the given filters.")
        return

    click.echo(result.summary())

    if write_signals:
        async with pool_context(settings) as pool:
            await write_calibration_signals(pool, result)
        click.echo("\nCalibration signals written to the signals table.")


@analytics.command(name="signals")
@click.option(
    "--category",
    default=None,
    help="Filter to markets with this category. Omit for all open markets.",
)
def signals_cmd(category: str | None) -> None:
    """Extract and persist implied-probability signals for open markets.

    For each open market, reads the latest quote tick and full book snapshot,
    then writes p_bid / p_ask / p_mid / microprice / depth_weighted_prob rows
    to the signals table.
    """
    asyncio.run(_run_signals(category=category))


async def _run_signals(*, category: str | None) -> None:
    from meridian.analytics.signals import run_signal_sweep

    settings = get_settings()
    configure_logging(settings)

    async with pool_context(settings) as pool:
        n = await run_signal_sweep(pool, category=category)

    click.echo(f"Signals written for {n} market(s).")
