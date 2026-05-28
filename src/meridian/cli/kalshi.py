"""CLI subcommands: `meridian kalshi {status,markets,orderbook}`."""

from __future__ import annotations

import asyncio
import sys

import click

from meridian.config import get_settings
from meridian.kalshi import KalshiClient, KalshiMarketStatus
from meridian.kalshi.errors import KalshiAuthError, KalshiHttpError
from meridian.logging import configure_logging, get_logger


@click.group(name="kalshi")
def kalshi() -> None:
    """Kalshi API utilities."""


@kalshi.command(name="status")
def status_cmd() -> None:
    """Fetch and print the Kalshi exchange status."""
    sys.exit(asyncio.run(_status()))


@kalshi.command(name="markets")
@click.option("--limit", default=10, type=int, show_default=True)
@click.option(
    "--status",
    "status_filter",
    type=click.Choice([s.value for s in KalshiMarketStatus]),
    default=None,
    help="Filter markets by status.",
)
def markets_cmd(limit: int, status_filter: str | None) -> None:
    """List markets, newest first."""
    status = KalshiMarketStatus(status_filter) if status_filter else None
    sys.exit(asyncio.run(_markets(limit, status)))


@kalshi.command(name="orderbook")
@click.argument("ticker")
def orderbook_cmd(ticker: str) -> None:
    """Fetch the order book for one market."""
    sys.exit(asyncio.run(_orderbook(ticker)))


async def _status() -> int:
    settings = get_settings()
    configure_logging(settings)
    log = get_logger("meridian.kalshi.cli")
    try:
        async with KalshiClient(settings) as client:
            info = await client.get_exchange_status()
    except (KalshiAuthError, KalshiHttpError) as exc:
        log.error("kalshi.status.failed", error=str(exc))
        return 1
    log.info("kalshi.status.ok", **info)
    return 0


async def _markets(limit: int, status: KalshiMarketStatus | None) -> int:
    settings = get_settings()
    configure_logging(settings)
    log = get_logger("meridian.kalshi.cli")
    try:
        async with KalshiClient(settings) as client:
            markets, cursor = await client.list_markets(limit=limit, status=status)
    except (KalshiAuthError, KalshiHttpError) as exc:
        log.error("kalshi.markets.failed", error=str(exc))
        return 1
    for m in markets:
        log.info(
            "market",
            ticker=m.ticker,
            status=m.status.value if m.status else None,
            yes_bid=m.yes_bid,
            yes_ask=m.yes_ask,
            last=m.last_price,
            volume_24h=m.volume_24h,
            title=m.title,
        )
    log.info("kalshi.markets.summary", count=len(markets), next_cursor=cursor)
    return 0


async def _orderbook(ticker: str) -> int:
    settings = get_settings()
    configure_logging(settings)
    log = get_logger("meridian.kalshi.cli")
    try:
        async with KalshiClient(settings) as client:
            book = await client.get_orderbook(ticker)
    except (KalshiAuthError, KalshiHttpError) as exc:
        log.error("kalshi.orderbook.failed", ticker=ticker, error=str(exc))
        return 1
    log.info(
        "kalshi.orderbook",
        ticker=ticker,
        yes_levels=len(book.yes),
        no_levels=len(book.no),
        yes_best_bid_cents=book.yes_best_bid_cents(),
        yes_best_ask_cents=book.yes_best_ask_cents(),
        yes_total_size=book.yes_total_size(),
        no_total_size=book.no_total_size(),
    )
    return 0
