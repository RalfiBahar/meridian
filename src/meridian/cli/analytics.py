"""CLI subcommands: `meridian analytics {calibrate,signals,microstructure,fedwatch}`."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
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


@analytics.command(name="fedwatch")
@click.option(
    "--date",
    "fomc_date",
    default=None,
    metavar="YYYY-MM-DD",
    help="FOMC meeting date to analyze. Defaults to the next upcoming date.",
)
@click.option(
    "--cme",
    is_flag=True,
    default=False,
    help="Also attempt to fetch CME FedWatch probabilities and compare.",
)
def fedwatch_cmd(fomc_date: str | None, cme: bool) -> None:
    """Show implied Fed-funds rate PMF from Kalshi KXFED contracts.

    Reads the latest p_mid signals for every contract in the KXFED partition
    group whose settlement date matches FOMC-DATE, sorts by strike, and
    displays the normalized probability mass function.

    Optionally compares against CME FedWatch-derived probabilities (--cme).
    CME fetch is best-effort and silently omitted if unavailable.
    """
    parsed_date: date | None = None
    if fomc_date is not None:
        try:
            parsed_date = date.fromisoformat(fomc_date)
        except ValueError:
            click.echo(f"Invalid date format: {fomc_date!r} (expected YYYY-MM-DD)", err=True)
            raise SystemExit(1) from None
    asyncio.run(_run_fedwatch(fomc_date=parsed_date, fetch_cme=cme))


async def _run_fedwatch(*, fomc_date: date | None, fetch_cme: bool) -> None:
    from meridian.analytics.fedwatch import build_kalshi_pmf, fetch_cme_fedwatch

    settings = get_settings()
    configure_logging(settings)

    # If no date given, use the nearest upcoming KXFED close date.
    if fomc_date is None:
        async with pool_context(settings) as pool:
            fomc_date = await _next_kxfed_date(pool)
        if fomc_date is None:
            click.echo("No open KXFED markets found in the database.", err=True)
            return

    async with pool_context(settings) as pool:
        pmf = await build_kalshi_pmf(pool, fomc_date)

    if pmf is None:
        click.echo(
            f"No KXFED contracts with p_mid signals found for {fomc_date}.", err=True
        )
        return

    click.echo(pmf.summary())

    if fetch_cme:
        cme_pmf = await fetch_cme_fedwatch(fomc_date)
        if cme_pmf is None:
            click.echo(
                "\nCME FedWatch: unavailable (network error or unrecognized format)."
            )
        else:
            click.echo("\n─── CME FedWatch comparison ───")
            click.echo(cme_pmf.summary())


async def _next_kxfed_date(pool: object) -> date | None:
    """Return the closes_at date of the nearest upcoming KXFED market."""
    async with pool.acquire() as conn:  # type: ignore[attr-defined]
        row = await conn.fetchrow(
            """
            SELECT DATE(closes_at) AS fomc_date
            FROM markets m
            JOIN venues v ON v.id = m.venue_id
            WHERE v.code = 'kalshi'
              AND m.external_id LIKE 'KXFED-%%'
              AND m.resolution_status = 'open'
              AND m.closes_at > now()
            ORDER BY m.closes_at ASC
            LIMIT 1
            """
        )
    if row is None:
        return None
    result: date = row["fomc_date"]
    return result


@analytics.command(name="anomaly")
@click.option(
    "--market",
    "ticker",
    default=None,
    metavar="TICKER",
    help="Restrict to a single market by external ID. Omit for all open markets.",
)
@click.option(
    "--window",
    default=7,
    show_default=True,
    type=int,
    metavar="DAYS",
    help="Rolling window in days.",
)
@click.option(
    "--contamination",
    default=0.05,
    show_default=True,
    type=float,
    help="Expected fraction of anomalies (IsolationForest contamination).",
)
@click.option(
    "--write-signals",
    is_flag=True,
    default=False,
    help="Persist anomaly_score rows to the signals table.",
)
def anomaly_cmd(
    ticker: str | None,
    window: int,
    contamination: float,
    write_signals: bool,
) -> None:
    """Detect anomalous market observations using Isolation Forest.

    Reads microprice, effective_spread, obi, kyle_lambda, and amihud signals
    from the signals table, groups them into hourly buckets per market, and
    fits an IsolationForest.  Observations with a negative decision-function
    score are flagged as anomalies.

    Requires prior runs of `meridian analytics signals` and
    `meridian analytics microstructure` to populate the signals table.
    """
    asyncio.run(
        _run_anomaly(
            ticker=ticker,
            window_days=window,
            contamination=contamination,
            write_signals=write_signals,
        )
    )


async def _run_anomaly(
    *,
    ticker: str | None,
    window_days: int,
    contamination: float,
    write_signals: bool,
) -> None:
    from meridian.analytics.anomaly import AnomalyReport, detect_anomalies, write_anomaly_signals

    settings = get_settings()
    configure_logging(settings)

    market_id: UUID | None = None
    if ticker is not None:
        async with pool_context(settings) as pool, pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id FROM markets WHERE external_id = $1 LIMIT 1", ticker
            )
        if row is None:
            click.echo(f"Market not found: {ticker}", err=True)
            return
        market_id = UUID(str(row["id"]))

    async with pool_context(settings) as pool:
        report: AnomalyReport | None = await detect_anomalies(
            pool,
            market_id=market_id,
            window=timedelta(days=window_days),
            contamination=contamination,
        )

    if report is None:
        click.echo(
            "Insufficient signal data — need at least 10 hourly observations. "
            "Run `meridian analytics signals` and `meridian analytics microstructure` first.",
            err=True,
        )
        return

    click.echo(report.summary())

    if write_signals:
        async with pool_context(settings) as pool:
            n = await write_anomaly_signals(pool, report)
        click.echo(f"\nWrote {n} anomaly_score signal rows.")


@analytics.command(name="regime")
@click.option(
    "--category",
    default=None,
    metavar="CATEGORY",
    help="Filter markets by category (e.g. 'fed'). Omit for all open markets.",
)
@click.option(
    "--n-states",
    default=3,
    show_default=True,
    type=int,
    help="Number of HMM states (typically 2 or 3).",
)
@click.option(
    "--window",
    default=30,
    show_default=True,
    type=int,
    metavar="DAYS",
    help="Rolling window in days.",
)
@click.option(
    "--write-signals",
    is_flag=True,
    default=False,
    help="Persist regime_state rows to the signals table.",
)
def regime_cmd(
    category: str | None,
    n_states: int,
    window: int,
    write_signals: bool,
) -> None:
    """Detect volatility regimes using a Gaussian Hidden Markov Model.

    Trains a k-state Gaussian HMM on effective_spread and obi signals for
    markets in the given category.  States are labeled low / medium / high
    by ascending mean effective_spread of each HMM component.  The current
    regime (most recent observation) is printed along with state frequencies.

    Requires prior runs of `meridian analytics microstructure` to populate
    effective_spread and obi signals.
    """
    asyncio.run(
        _run_regime(
            category=category,
            n_states=n_states,
            window_days=window,
            write_signals=write_signals,
        )
    )


async def _run_regime(
    *,
    category: str | None,
    n_states: int,
    window_days: int,
    write_signals: bool,
) -> None:
    from meridian.analytics.regime import RegimeResult, detect_regimes, write_regime_signals

    settings = get_settings()
    configure_logging(settings)

    async with pool_context(settings) as pool:
        result: RegimeResult | None = await detect_regimes(
            pool,
            category=category,
            n_states=n_states,
            window=timedelta(days=window_days),
        )

    if result is None:
        click.echo(
            "Insufficient signal data — need at least 20 hourly observations. "
            "Run `meridian analytics microstructure` first.",
            err=True,
        )
        return

    click.echo(result.summary())

    if write_signals:
        async with pool_context(settings) as pool:
            n = await write_regime_signals(pool, result)
        click.echo(f"\nWrote {n} regime_state signal rows.")


@analytics.command(name="marketmaker")
@click.argument("ticker")
@click.option(
    "--half-spread",
    default="0.01",
    show_default=True,
    metavar="DECIMAL",
    help="Half-spread posted around midprice (e.g. 0.01 = 1 cent).",
)
@click.option(
    "--base-size",
    default=10,
    show_default=True,
    type=int,
    help="Normal order size in contracts.",
)
@click.option(
    "--max-inventory",
    default=100,
    show_default=True,
    type=int,
    help="Maximum net position; quoting suppressed at the limit.",
)
@click.option(
    "--window",
    default=7,
    show_default=True,
    type=int,
    metavar="DAYS",
    help="Historical window for the backtest.",
)
def marketmaker_cmd(
    ticker: str,
    half_spread: str,
    base_size: int,
    max_inventory: int,
    window: int,
) -> None:
    """Run a market-making backtest against historical ticks.

    TICKER is the market's external ID (Kalshi ticker or Polymarket token ID).

    Posts symmetric quotes around the midprice with HALF_SPREAD on each side.
    Fills are simulated when a historical trade crosses a posted quote.
    Inventory management reduces size and suppresses one-sided quoting when
    the position approaches MAX_INVENTORY.

    Prints realized P&L, MTM P&L, fill rate, and annualised Sharpe.
    """
    asyncio.run(
        _run_marketmaker(
            ticker=ticker,
            half_spread=Decimal(half_spread),
            base_size=Decimal(str(base_size)),
            max_inventory=Decimal(str(max_inventory)),
            window_days=window,
        )
    )


async def _run_marketmaker(
    *,
    ticker: str,
    half_spread: Decimal,
    base_size: Decimal,
    max_inventory: Decimal,
    window_days: int,
) -> None:
    from meridian.research.marketmaker import MarketMakerConfig, run_mm_backtest

    settings = get_settings()
    configure_logging(settings)

    async with pool_context(settings) as pool, pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id FROM markets WHERE external_id = $1 LIMIT 1", ticker
        )
    if row is None:
        click.echo(f"Market not found: {ticker}", err=True)
        return

    market_id = UUID(str(row["id"]))
    config = MarketMakerConfig(
        half_spread=half_spread,
        base_size=base_size,
        max_inventory=max_inventory,
    )

    async with pool_context(settings) as pool:
        result = await run_mm_backtest(
            pool,
            market_id,
            config=config,
            window=timedelta(days=window_days),
        )

    if result is None:
        click.echo(
            f"No quote ticks found for {ticker} in the last {window_days} days.",
            err=True,
        )
        return

    click.echo(result.summary())


@analytics.command(name="nlp-tag")
@click.option(
    "--category",
    default=None,
    help="Filter to events in this category. Omit for all.",
)
@click.option(
    "--train-window",
    default=90,
    show_default=True,
    type=int,
    metavar="DAYS",
    help="Training window in days (historical events with measured price delta).",
)
@click.option(
    "--tag-window",
    default=7,
    show_default=True,
    type=int,
    metavar="DAYS",
    help="Window of recent events to classify.",
)
@click.option(
    "--price-threshold",
    default=0.02,
    show_default=True,
    type=float,
    metavar="FLOAT",
    help="Abs delta_p threshold for 'market-moving' label during training.",
)
@click.option(
    "--write-signals",
    is_flag=True,
    default=False,
    help="Persist market_moving_prob signals to the signals table.",
)
def nlp_tag_cmd(
    category: str | None,
    train_window: int,
    tag_window: int,
    price_threshold: float,
    write_signals: bool,
) -> None:
    """Train a news tagger and classify recent events as market-moving or not.

    Trains a TF-IDF + LogisticRegression pipeline on historical news events
    paired with their measured p_mid delta.  Then classifies events in the
    last TAG_WINDOW days and prints probabilities.
    """
    asyncio.run(
        _run_nlp_tag(
            category=category,
            train_window_days=train_window,
            tag_window_days=tag_window,
            price_threshold=price_threshold,
            write_signals=write_signals,
        )
    )


async def _run_nlp_tag(
    *,
    category: str | None,
    train_window_days: int,
    tag_window_days: int,
    price_threshold: float,
    write_signals: bool,
) -> None:
    from meridian.analytics.nlp import NewsTaggerConfig, tag_recent_events, train_tagger
    from meridian.analytics.nlp import write_nlp_signals as _write_signals

    settings = get_settings()
    configure_logging(settings)

    async with pool_context(settings) as pool:
        config = NewsTaggerConfig(price_delta_threshold=price_threshold)
        tagger = await train_tagger(
            pool,
            category=category,
            window=timedelta(days=train_window_days),
            config=config,
        )
        if tagger is None:
            click.echo(
                "Not enough training data (need >= 10 events with measured price delta).",
                err=True,
            )
            return

        click.echo(tagger.summary())
        click.echo()

        results = await tag_recent_events(
            pool,
            tagger,
            category=category,
            window=timedelta(days=tag_window_days),
        )

    if not results:
        click.echo(f"No news events in the last {tag_window_days} days.")
        return

    click.echo(f"{'Label':<50} {'Category':<15} {'P(moving)':>10} {'Moving?':>8}")
    click.echo("-" * 87)
    for r in sorted(results, key=lambda x: x.market_moving_prob, reverse=True):
        flag = "YES" if r.is_market_moving else "no"
        click.echo(f"{r.label[:50]:<50} {r.category:<15} {r.market_moving_prob:>10.3f} {flag:>8}")

    n_moving = sum(1 for r in results if r.is_market_moving)
    click.echo(f"\n{n_moving}/{len(results)} events classified as market-moving.")

    if write_signals:
        async with pool_context(settings) as pool:
            n = await _write_signals(pool, results)
        click.echo(f"Wrote {n} market_moving_prob signals.")


@analytics.command(name="event-response")
@click.argument("event_id")
@click.option(
    "--pre",
    default=60,
    show_default=True,
    type=int,
    metavar="MINUTES",
    help="Pre-event window in minutes.",
)
@click.option(
    "--post",
    default=60,
    show_default=True,
    type=int,
    metavar="MINUTES",
    help="Post-event window in minutes.",
)
def event_response_cmd(event_id: str, pre: int, post: int) -> None:
    """Measure market reaction to a news event.

    EVENT_ID is the UUID of a row in the `news_events` table.

    Computes mean ΔP_mid and variance ratio (post/pre) across all open
    markets in the same category as the event.  Variance ratio > 1 indicates
    that the event introduced new information.
    """
    try:
        eid = UUID(event_id)
    except ValueError:
        click.echo(f"Invalid UUID: {event_id!r}", err=True)
        raise SystemExit(1) from None
    asyncio.run(
        _run_event_response(
            event_id=eid,
            pre_window=timedelta(minutes=pre),
            post_window=timedelta(minutes=post),
        )
    )


async def _run_event_response(
    *,
    event_id: UUID,
    pre_window: timedelta,
    post_window: timedelta,
) -> None:
    from meridian.analytics.fedwatch import compute_event_response

    settings = get_settings()
    configure_logging(settings)

    async with pool_context(settings) as pool:
        result = await compute_event_response(
            pool, event_id, pre_window=pre_window, post_window=post_window
        )

    if result is None:
        click.echo(f"News event not found: {event_id}", err=True)
        return

    click.echo(result.summary())
