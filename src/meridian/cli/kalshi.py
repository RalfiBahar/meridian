"""CLI subcommands: `meridian kalshi {status,markets,orderbook,tap}`."""

from __future__ import annotations

import asyncio
import signal
import sys
import time

import click

from meridian.config import get_settings
from meridian.db.postgres import pool_context
from meridian.ingest import KalshiIngestWorker
from meridian.kalshi import KalshiClient, KalshiWebSocketClient, normalize_kalshi_message
from meridian.kalshi.errors import KalshiAuthError, KalshiHttpError
from meridian.kalshi.ws import DEFAULT_CHANNELS
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


@kalshi.command(name="tap")
@click.option(
    "--tickers",
    required=True,
    help="Comma-separated Kalshi market tickers to subscribe to.",
)
@click.option(
    "--seconds",
    type=int,
    default=30,
    show_default=True,
    help="Run duration; disconnect cleanly after this many seconds.",
)
@click.option(
    "--channels",
    default=",".join(DEFAULT_CHANNELS),
    show_default=True,
    help="Comma-separated WS channels to subscribe to.",
)
def tap_cmd(tickers: str, seconds: int, channels: str) -> None:
    """Subscribe to Kalshi WS, normalize each message, log it. Read-only — no DB writes."""
    ticker_list = [t.strip() for t in tickers.split(",") if t.strip()]
    channel_tuple = tuple(c.strip() for c in channels.split(",") if c.strip())
    if not ticker_list:
        click.echo("error: --tickers must contain at least one ticker", err=True)
        sys.exit(2)
    sys.exit(asyncio.run(_tap(ticker_list, seconds, channel_tuple)))


@kalshi.command(name="ingest")
@click.option(
    "--tickers",
    required=True,
    help="Comma-separated Kalshi market tickers to subscribe to.",
)
@click.option(
    "--channels",
    default=",".join(DEFAULT_CHANNELS),
    show_default=True,
)
def ingest_cmd(tickers: str, channels: str) -> None:
    """Subscribe to Kalshi WS, normalize, and persist to TimescaleDB.

    Runs until SIGINT (Ctrl-C) or SIGTERM. Prefer `meridian ingest kalshi`
    for new scripts; this alias is kept for backward compatibility.
    """
    ticker_list = [t.strip() for t in tickers.split(",") if t.strip()]
    channel_tuple = tuple(c.strip() for c in channels.split(",") if c.strip())
    if not ticker_list:
        click.echo("error: --tickers must contain at least one ticker", err=True)
        sys.exit(2)
    sys.exit(asyncio.run(_ingest(ticker_list, channel_tuple)))


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


async def _tap(tickers: list[str], seconds: int, channels: tuple[str, ...]) -> int:
    settings = get_settings()
    configure_logging(settings)
    log = get_logger("meridian.kalshi.tap")
    log.info("tap.start", tickers=tickers, seconds=seconds, channels=list(channels))

    deadline = time.monotonic() + seconds
    received = 0
    normalized = 0
    control = 0
    unknown_types: dict[str, int] = {}

    try:
        async with KalshiWebSocketClient(settings, tickers=tickers, channels=channels) as client:
            stream = client.stream()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    raw = await asyncio.wait_for(stream.__anext__(), timeout=remaining)
                except (TimeoutError, StopAsyncIteration):
                    break
                received += 1
                event = normalize_kalshi_message(raw)
                if event is None:
                    raw_type = raw.get("type", "?")
                    msg_type = raw_type if isinstance(raw_type, str) else "?"
                    if msg_type in ("subscribed", "ok", "unsubscribed"):
                        control += 1
                        log.info("tap.control", type=msg_type, msg=raw.get("msg"))
                    else:
                        unknown_types[msg_type] = unknown_types.get(msg_type, 0) + 1
                    continue
                normalized += 1
                _log_event(log, event)
    except KalshiAuthError as exc:
        log.error("tap.auth_failed", error=str(exc))
        return 1

    log.info(
        "tap.done",
        received=received,
        normalized=normalized,
        control=control,
        unknown=unknown_types,
    )
    return 0


def _log_event(log: object, event: object) -> None:
    """Log a CanonicalEvent in a flat shape — kind + key payload fields."""
    from meridian.events import (
        BookDeltaEvent,
        BookEvent,
        CanonicalEvent,
        QuoteEvent,
        StatusEvent,
        TradeEvent,
    )

    assert isinstance(event, CanonicalEvent)
    payload = event.payload
    fields: dict[str, object] = {
        "ticker": event.external_market_id,
        "seq": event.sequence_no,
        "kind": payload.kind.value,
    }
    if isinstance(payload, QuoteEvent):
        fields["bid"] = str(payload.bid) if payload.bid is not None else None
        fields["ask"] = str(payload.ask) if payload.ask is not None else None
        fields["bid_size"] = str(payload.bid_size) if payload.bid_size is not None else None
        fields["ask_size"] = str(payload.ask_size) if payload.ask_size is not None else None
    elif isinstance(payload, TradeEvent):
        fields["price"] = str(payload.price)
        fields["size"] = str(payload.size)
        fields["aggressor"] = payload.aggressor
    elif isinstance(payload, BookEvent):
        fields["levels"] = len(payload.levels)
        yes_levels = [lv for lv in payload.levels if lv.side == "yes"]
        no_levels = [lv for lv in payload.levels if lv.side == "no"]
        fields["yes_best"] = str(yes_levels[0].price) if yes_levels else None
        fields["no_best"] = str(no_levels[0].price) if no_levels else None
    elif isinstance(payload, BookDeltaEvent):
        fields["side"] = payload.side
        fields["price"] = str(payload.price)
        fields["delta"] = str(payload.delta)
    elif isinstance(payload, StatusEvent):
        fields["status"] = payload.status
    # `log` is structlog's FilteringBoundLogger but typed as object to keep
    # the helper signature simple. The runtime call is correct.
    log.info("tap.event", **fields)  # type: ignore[attr-defined]


async def _ingest(tickers: list[str], channels: tuple[str, ...]) -> int:
    settings = get_settings()
    configure_logging(settings)
    log = get_logger("meridian.kalshi.ingest")
    log.info("ingest.start", tickers=tickers, channels=list(channels))

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    try:
        async with pool_context(settings) as pool:
            worker = KalshiIngestWorker(settings, pool, tickers=tickers, channels=channels)
            stats = await worker.run(stop_event=stop_event)
    except KalshiAuthError as exc:
        log.error("ingest.auth_failed", error=str(exc))
        return 1

    log.info("ingest.done", **stats.as_log_fields())
    return 0
