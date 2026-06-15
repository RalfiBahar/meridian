"""CLI subcommands: `meridian ingest {kalshi,...}`."""

from __future__ import annotations

import asyncio
import signal
import sys

import click

from meridian.bus.redis import client_context
from meridian.config import get_settings
from meridian.db.postgres import pool_context
from meridian.ingest import KalshiIngestWorker
from meridian.kalshi.errors import KalshiAuthError
from meridian.kalshi.ws import DEFAULT_CHANNELS
from meridian.logging import configure_logging, get_logger


@click.group(name="ingest")
def ingest() -> None:
    """Long-running ingest workers (run until interrupted)."""


@ingest.command(name="kalshi")
@click.option(
    "--tickers",
    required=True,
    help="Comma-separated Kalshi market tickers to subscribe to.",
)
@click.option(
    "--channels",
    default=",".join(DEFAULT_CHANNELS),
    show_default=True,
    help="Comma-separated WS channels to subscribe to.",
)
@click.option(
    "--no-redis",
    is_flag=True,
    default=False,
    help="Disable Redis Streams publishing (useful when Redis is unavailable).",
)
def kalshi_cmd(tickers: str, channels: str, no_redis: bool) -> None:
    """Subscribe to Kalshi WS, normalize, persist to DB, and publish to Redis Streams.

    Runs until SIGINT (Ctrl-C) or SIGTERM. Reconnects automatically with
    exponential backoff on connection drops.
    """
    ticker_list = [t.strip() for t in tickers.split(",") if t.strip()]
    channel_tuple = tuple(c.strip() for c in channels.split(",") if c.strip())
    if not ticker_list:
        click.echo("error: --tickers must contain at least one ticker", err=True)
        sys.exit(2)
    sys.exit(asyncio.run(_run_kalshi(ticker_list, channel_tuple, use_redis=not no_redis)))


async def _run_kalshi(
    tickers: list[str],
    channels: tuple[str, ...],
    *,
    use_redis: bool,
) -> int:
    settings = get_settings()
    configure_logging(settings)
    log = get_logger("meridian.ingest.kalshi")

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    log.info("ingest.starting", tickers=tickers, channels=list(channels))
    try:
        async with pool_context(settings) as pool:
            if use_redis:
                async with client_context(settings) as redis_client:
                    worker = KalshiIngestWorker(
                        settings,
                        pool,
                        tickers=tickers,
                        channels=channels,
                        redis=redis_client,
                    )
                    stats = await worker.run(stop_event=stop_event)
            else:
                worker = KalshiIngestWorker(
                    settings, pool, tickers=tickers, channels=channels
                )
                stats = await worker.run(stop_event=stop_event)
    except KalshiAuthError as exc:
        log.error("ingest.auth_failed", error=str(exc))
        return 1

    log.info("ingest.stopped", **stats.as_log_fields())
    return 0
