"""CLI subcommands: `meridian experiment {run,list,portfolio}`."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import click

from meridian.config import get_settings
from meridian.db.postgres import pool_context
from meridian.logging import configure_logging


@click.group(name="experiment")
def experiment() -> None:
    """Reproducible experiment runner and portfolio optimizer."""


@experiment.command(name="run")
@click.argument("name")
@click.option(
    "--params",
    default=None,
    metavar="JSON",
    help="JSON object of experiment parameters (overrides manifest.yaml defaults).",
)
@click.option(
    "--window-start",
    default=None,
    metavar="DATE",
    help="Data window start (ISO date, e.g. 2026-01-01).",
)
@click.option(
    "--window-end",
    default=None,
    metavar="DATE",
    help="Data window end (ISO date).",
)
@click.option(
    "--notes",
    default=None,
    help="Free-text notes attached to this run.",
)
@click.option(
    "--timeout",
    default=300,
    show_default=True,
    type=int,
    metavar="SECONDS",
    help="Maximum run time before the subprocess is killed.",
)
def run_cmd(
    name: str,
    params: str | None,
    window_start: str | None,
    window_end: str | None,
    notes: str | None,
    timeout: int,
) -> None:
    """Run the named experiment and persist the result.

    NAME must match a directory under `experiments/` that contains `run.py`.

    The experiment script is executed with `EXPERIMENT_PARAMS` set to a JSON
    string of parameters.  If the last line of stdout is a JSON object, it is
    captured as the `metrics` payload.
    """
    parsed_params: dict[str, object] = {}
    if params is not None:
        try:
            parsed_params = json.loads(params)
        except json.JSONDecodeError as e:
            click.echo(f"--params is not valid JSON: {e}", err=True)
            raise SystemExit(1) from None

    data_window: dict[str, object] = {}
    if window_start:
        data_window["start"] = window_start
    if window_end:
        data_window["end"] = window_end

    asyncio.run(
        _run_experiment(
            name=name,
            params=parsed_params,
            data_window=data_window,
            notes=notes,
            run_timeout=float(timeout),
        )
    )


async def _run_experiment(
    *,
    name: str,
    params: dict[str, object],
    data_window: dict[str, object],
    notes: str | None,
    run_timeout: float,
) -> None:
    from meridian.research.experiment import run_experiment

    settings = get_settings()
    configure_logging(settings)

    try:
        async with pool_context(settings) as pool:
            result = await run_experiment(
                pool,
                name,
                params=params,
                data_window=data_window,
                notes=notes,
                run_timeout=run_timeout,
            )
    except FileNotFoundError as exc:
        click.echo(str(exc), err=True)
        raise SystemExit(1) from None

    click.echo(result.summary())
    if not result.success:
        raise SystemExit(1) from None


@experiment.command(name="list")
@click.option(
    "--name",
    default=None,
    help="Filter to experiments with this name.",
)
@click.option(
    "--limit",
    default=20,
    show_default=True,
    type=int,
    help="Maximum rows to display.",
)
def list_cmd(name: str | None, limit: int) -> None:
    """List recent experiment runs from the database."""
    asyncio.run(_run_list(name=name, limit=limit))


async def _run_list(*, name: str | None, limit: int) -> None:
    from meridian.research.experiment import list_experiments

    settings = get_settings()
    configure_logging(settings)

    async with pool_context(settings) as pool:
        rows = await list_experiments(pool, name=name, limit=limit)

    if not rows:
        click.echo("No experiments found.")
        return

    click.echo(f"{'Name':<30}  {'Status':<10}  {'Created':^24}  {'Metrics summary'}")
    click.echo("─" * 90)
    for r in rows:
        metrics = r.get("metrics") or {}
        metrics_str = ", ".join(f"{k}={v}" for k, v in list(metrics.items())[:3])
        created = r["created_at"].isoformat()[:19] if r.get("created_at") else "?"
        click.echo(f"{str(r['name'])[:29]:<30}  {r['status']!s:<10}  {created:<24}  {metrics_str}")


@experiment.command(name="export")
@click.argument("id_or_name")
@click.option(
    "--format",
    "fmt",
    default="json",
    type=click.Choice(["json", "md"], case_sensitive=False),
    show_default=True,
    help="Output format: json or md (Markdown).",
)
@click.option(
    "--output",
    "-o",
    default=None,
    metavar="FILE",
    help="Write to FILE instead of stdout.",
)
def export_cmd(id_or_name: str, fmt: str, output: str | None) -> None:
    """Export experiment params, metrics, and SHA to JSON or Markdown.

    ID_OR_NAME can be a numeric row ID or an experiment name (latest run).
    """
    asyncio.run(_run_export(id_or_name=id_or_name, fmt=fmt, output=output))


async def _run_export(*, id_or_name: str, fmt: str, output: str | None) -> None:
    settings = get_settings()
    configure_logging(settings)

    async with pool_context(settings) as pool:
        # Try numeric ID first, then fall back to name lookup.
        try:
            row_id = int(id_or_name)
            query = "SELECT * FROM experiments WHERE id = $1 LIMIT 1"
            param: Any = row_id
        except ValueError:
            query = "SELECT * FROM experiments WHERE name = $1 ORDER BY created_at DESC LIMIT 1"
            param = id_or_name

        async with pool.acquire() as conn:
            row = await conn.fetchrow(query, param)

    if row is None:
        click.echo(f"No experiment found for '{id_or_name}'.", err=True)
        raise SystemExit(1)

    record: dict[str, Any] = dict(row)
    # Normalise datetime fields to ISO strings for serialisation.
    for k, v in record.items():
        if hasattr(v, "isoformat"):
            record[k] = v.isoformat()

    if fmt == "json":
        text = json.dumps(record, indent=2, default=str)
    else:
        lines: list[str] = [
            f"# Experiment: {record.get('name', 'unknown')}",
            "",
            f"| Field | Value |",
            f"|-------|-------|",
            f"| ID | {record.get('id', '—')} |",
            f"| Status | {record.get('status', '—')} |",
            f"| Git SHA | `{record.get('code_sha') or '—'}` |",
            f"| Data window | {record.get('data_window') or '—'} |",
            f"| Started | {record.get('started_at') or '—'} |",
            f"| Completed | {record.get('completed_at') or '—'} |",
            "",
            "## Parameters",
            "",
            "```json",
            json.dumps(record.get("params") or {}, indent=2),
            "```",
            "",
            "## Metrics",
            "",
            "```json",
            json.dumps(record.get("metrics") or {}, indent=2),
            "```",
            "",
        ]
        if record.get("notes"):
            lines += ["## Notes", "", str(record["notes"]), ""]
        text = "\n".join(lines)

    if output:
        import pathlib

        pathlib.Path(output).write_text(text, encoding="utf-8")
        click.echo(f"Written to {output}")
    else:
        click.echo(text)


@experiment.command(name="portfolio")
@click.option(
    "--category",
    required=True,
    help="Market category to optimize over (e.g. 'fed').",
)
@click.option(
    "--lookback",
    default=90,
    show_default=True,
    type=int,
    metavar="DAYS",
    help="Lookback window in days for return estimation.",
)
@click.option(
    "--target-return",
    default=None,
    type=float,
    help="Optional minimum target return constraint.",
)
@click.option(
    "--no-shrink",
    is_flag=True,
    default=False,
    help="Use plain sample covariance instead of Ledoit-Wolf shrinkage.",
)
@click.option(
    "--frontier",
    is_flag=True,
    default=False,
    help="Plot the efficient frontier (20 points) instead of one portfolio.",
)
@click.option(
    "--walk-forward",
    "walk_forward",
    is_flag=True,
    default=False,
    help="Run walk-forward cross-validation (60-day train, 21-day test, 21-day step).",
)
def portfolio_cmd(
    category: str,
    lookback: int,
    target_return: float | None,
    no_shrink: bool,
    frontier: bool,
    walk_forward: bool,
) -> None:
    """Run the Markowitz mean-variance portfolio optimizer.

    Reads p_mid signals for all open markets in CATEGORY over the last LOOKBACK
    days, constructs a return series, and optimizes the minimum-variance portfolio
    with Ledoit-Wolf covariance shrinkage (OAS).

    Optionally runs walk-forward evaluation (--walk-forward) with a 60-day
    expanding training window and 21-day out-of-sample test period.
    """
    asyncio.run(
        _run_portfolio(
            category=category,
            lookback_days=lookback,
            target_return=target_return,
            shrink=not no_shrink,
            frontier=frontier,
            walk_forward=walk_forward,
        )
    )


async def _run_portfolio(
    *,
    category: str,
    lookback_days: int,
    target_return: float | None,
    shrink: bool,
    frontier: bool,
    walk_forward: bool,
) -> None:

    from meridian.research.portfolio import efficient_frontier, optimize
    from meridian.research.walkforward import evaluate, make_folds

    settings = get_settings()
    configure_logging(settings)

    async with pool_context(settings) as pool:
        returns = await _fetch_returns(pool, category=category, lookback_days=lookback_days)

    if returns is None or returns.shape[0] < 5:
        click.echo(
            f"Insufficient data for category '{category}' "
            f"(need >= 5 observations, got {returns.shape[0] if returns is not None else 0}).",
            err=True,
        )
        return

    T, N = returns.shape
    click.echo(f"Loaded {T} observations x {N} markets for category '{category}'.")

    if walk_forward:
        train_size = min(60, T // 2)
        test_size = max(5, T // 10)
        try:
            folds = make_folds(returns, train_size=train_size, test_size=test_size)
        except ValueError as e:
            click.echo(str(e), err=True)
            return
        summary = evaluate(folds, optimize_fn=optimize, shrink=shrink)
        click.echo(summary.summary())
        return

    if frontier:
        results = efficient_frontier(returns, n_points=20, shrink=shrink)
        click.echo(f"\n{'Vol':>8}  {'E[ret]':>8}  {'Sharpe':>8}")
        click.echo("─" * 28)
        for r in results:
            sharpe_str = f"{r.sharpe:.3f}" if r.sharpe is not None else " n/a"
            click.echo(f"{r.expected_vol:>8.4f}  {r.expected_return:>+8.4f}  {sharpe_str:>8}")
        return

    result = optimize(returns, target_return=target_return, shrink=shrink)
    click.echo(result.summary())


async def _fetch_returns(
    pool: object,
    *,
    category: str,
    lookback_days: int,
) -> Any:
    """Read p_mid signals → return matrix as numpy array; returns None if insufficient data."""
    from datetime import UTC, datetime, timedelta

    import numpy as np

    since = datetime.now(tz=UTC) - timedelta(days=lookback_days)

    async with pool.acquire() as conn:  # type: ignore[attr-defined]
        rows = await conn.fetch(
            """
            SELECT s.market_id, s.event_ts, s.value
            FROM signals s
            JOIN markets m ON m.id = s.market_id
            WHERE m.category = $1
              AND s.signal_type = 'p_mid'
              AND s.event_ts >= $2
              AND s.value IS NOT NULL
            ORDER BY s.event_ts, s.market_id
            """,
            category,
            since,
        )

    if not rows:
        return None

    # Pivot to (time, market) matrix; use daily p_mid returns = Δp_mid.
    import pandas as pd

    df = pd.DataFrame(
        {
            "market_id": [str(r["market_id"]) for r in rows],
            "event_ts": [r["event_ts"] for r in rows],
            "value": [float(r["value"]) for r in rows],
        }
    )
    df["date"] = df["event_ts"].dt.normalize()
    daily = df.groupby(["date", "market_id"])["value"].last().unstack(fill_value=None)
    daily = daily.ffill().dropna(how="all")

    # At least 2 time points needed for returns.
    if len(daily) < 2:
        return None

    prices = daily.to_numpy(dtype=float, na_value=float("nan"))
    # Replace NaN columns (always missing) with 0.
    prices = np.where(np.isnan(prices), np.nanmean(prices, axis=0, keepdims=True), prices)
    returns: np.ndarray = np.diff(prices, axis=0)  # first difference of probabilities
    return returns
