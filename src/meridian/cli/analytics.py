"""CLI subcommands: `meridian analytics {calibrate,signals,microstructure}`."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

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


@analytics.command(name="microstructure")
@click.argument("ticker")
@click.option(
    "--window",
    default=7,
    show_default=True,
    type=int,
    metavar="DAYS",
    help="Rolling window in days.",
)
@click.option(
    "--simulate-buy",
    default=None,
    type=float,
    metavar="QUANTITY",
    help="Simulate a buy order of this size against the current book.",
)
@click.option(
    "--simulate-sell",
    default=None,
    type=float,
    metavar="QUANTITY",
    help="Simulate a sell order of this size against the current book.",
)
@click.option(
    "--write-signals",
    is_flag=True,
    default=False,
    help="Persist computed metrics to the signals table.",
)
def microstructure_cmd(
    ticker: str,
    window: int,
    simulate_buy: float | None,
    simulate_sell: float | None,
    write_signals: bool,
) -> None:
    """Compute microstructure metrics for a market over a rolling window.

    TICKER is the market's external ID (Kalshi ticker or Polymarket token ID).

    Metrics: effective spread (ask-bid), order-book imbalance (OBI),
    Kyle's lambda (price impact per unit signed volume), and Amihud
    illiquidity ratio (|return|/volume).

    Optionally simulate a buy or sell order against the current book to
    estimate fill price and slippage.
    """
    asyncio.run(
        _run_microstructure(
            ticker=ticker,
            window_days=window,
            simulate_buy=Decimal(str(simulate_buy)) if simulate_buy is not None else None,
            simulate_sell=Decimal(str(simulate_sell)) if simulate_sell is not None else None,
            write_signals=write_signals,
        )
    )


async def _run_microstructure(
    *,
    ticker: str,
    window_days: int,
    simulate_buy: Decimal | None,
    simulate_sell: Decimal | None,
    write_signals: bool,
) -> None:
    from meridian.analytics.microstructure import (
        MicrostructureMetrics,
        compute_microstructure,
        simulate_execution,
    )

    settings = get_settings()
    configure_logging(settings)

    async with pool_context(settings) as pool:
        # Resolve ticker to market_id.
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id FROM markets WHERE external_id = $1 LIMIT 1", ticker
            )
        if row is None:
            click.echo(f"Market not found: {ticker}", err=True)
            return

        market_id = UUID(str(row["id"]))
        window = timedelta(days=window_days)
        m: MicrostructureMetrics = await compute_microstructure(pool, market_id, window=window)

        if write_signals:
            from meridian.analytics.microstructure import _write_signals

            await _write_signals(pool, m)

    click.echo(f"Market:          {ticker}")
    click.echo(f"Window:          {window_days} days")
    click.echo(f"Quotes:          {m.n_quotes}")
    click.echo(f"Trades:          {m.n_trades}")
    spread = f"{float(m.effective_spread):.4f}" if m.effective_spread is not None else "n/a"
    obi = f"{float(m.obi):.4f}" if m.obi is not None else "n/a"
    kyle = f"{m.kyle_lambda:.6f}" if m.kyle_lambda is not None else "n/a"
    amihud = f"{m.amihud:.8f}" if m.amihud is not None else "n/a"
    click.echo(f"Effective spread: {spread}")
    click.echo(f"OBI:             {obi}")
    click.echo(f"Kyle's lambda:   {kyle}")
    click.echo(f"Amihud:          {amihud}")

    async with pool_context(settings) as pool:
        for side, qty in (("buy", simulate_buy), ("sell", simulate_sell)):
            if qty is None:
                continue
            est = await simulate_execution(pool, market_id, side=side, target_quantity=qty)
            click.echo(f"\n--- Execution sim ({side} {qty}) ---")
            click.echo(f"  Filled:        {est.filled_quantity} / {est.target_quantity}")
            click.echo(f"  Avg fill:      {est.avg_fill_price}")
            click.echo(f"  Best quote:    {est.best_quote}")
            click.echo(f"  Slippage:      {est.slippage}")
            click.echo(f"  Levels:        {est.n_levels_consumed}")
            if est.partially_filled:
                click.echo("  (partially filled — insufficient book depth)")
