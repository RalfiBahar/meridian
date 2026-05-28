"""CLI subcommands: `meridian kalshi {status,markets,orderbook}`."""

from __future__ import annotations

import asyncio
import sys

import click

from meridian.config import get_settings
from meridian.kalshi import KalshiClient
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
    type=str,
    default=None,
    help="Filter by status: 'unopened', 'open', 'closed', 'settled', or comma-separated.",
)
def markets_cmd(limit: int, status_filter: str | None) -> None:
    """List markets, newest first."""
    sys.exit(asyncio.run(_markets(limit, status_filter)))


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


async def _markets(limit: int, status: str | None) -> int:
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
            yes_bid=str(m.yes_bid) if m.yes_bid is not None else None,
            yes_ask=str(m.yes_ask) if m.yes_ask is not None else None,
            last=str(m.last_price) if m.last_price is not None else None,
            volume_24h=str(m.volume_24h) if m.volume_24h is not None else None,
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
        yes_best_bid=str(book.yes_best_bid()) if book.yes_best_bid() is not None else None,
        yes_best_ask=str(book.yes_best_ask()) if book.yes_best_ask() is not None else None,
        yes_spread=str(book.yes_spread()) if book.yes_spread() is not None else None,
        yes_total_size=str(book.yes_total_size()),
        no_total_size=str(book.no_total_size()),
    )
    return 0
