"""CLI subcommands: `meridian polymarket {markets,orderbook,tap}`."""

from __future__ import annotations

import asyncio
import sys
import time

import click

from meridian.config import get_settings
from meridian.logging import configure_logging, get_logger
from meridian.polymarket import PolymarketClient
from meridian.polymarket.errors import PolymarketHttpError
from meridian.polymarket.normalize import PolymarketBookState, normalize_polymarket_message
from meridian.polymarket.ws import PolymarketWebSocketClient


@click.group(name="polymarket")
def polymarket() -> None:
    """Polymarket CLOB API utilities."""


@polymarket.command(name="markets")
@click.option("--limit", default=10, type=int, show_default=True)
@click.option(
    "--active-only/--all",
    default=True,
    show_default=True,
    help="Filter to active, non-closed markets (client-side; the API has no server filter).",
)
def markets_cmd(limit: int, active_only: bool) -> None:
    """List markets from the first page of `/markets`."""
    sys.exit(asyncio.run(_markets(limit, active_only)))


@polymarket.command(name="orderbook")
@click.argument("token_id")
def orderbook_cmd(token_id: str) -> None:
    """Fetch the order book for one outcome token."""
    sys.exit(asyncio.run(_orderbook(token_id)))


@polymarket.command(name="tap")
@click.option(
    "--assets",
    required=True,
    help="Comma-separated Polymarket token (asset) IDs to subscribe to.",
)
@click.option(
    "--seconds",
    type=int,
    default=30,
    show_default=True,
    help="Run duration; disconnect cleanly after this many seconds.",
)
def tap_cmd(assets: str, seconds: int) -> None:
    """Subscribe to the Polymarket WS, normalize each message, log it. Read-only — no DB writes."""
    asset_list = [a.strip() for a in assets.split(",") if a.strip()]
    if not asset_list:
        click.echo("error: --assets must contain at least one token ID", err=True)
        sys.exit(2)
    sys.exit(asyncio.run(_tap(asset_list, seconds)))


async def _markets(limit: int, active_only: bool) -> int:
    settings = get_settings()
    configure_logging(settings)
    log = get_logger("meridian.polymarket.cli")
    try:
        async with PolymarketClient(settings) as client:
            markets, cursor = await client.list_markets()
    except PolymarketHttpError as exc:
        log.error("polymarket.markets.failed", error=str(exc))
        return 1
    if active_only:
        markets = [m for m in markets if m.active and not m.closed]
    for m in markets[:limit]:
        log.info(
            "market",
            condition_id=m.condition_id,
            question=m.question,
            active=m.active,
            closed=m.closed,
            tokens=[(t.token_id, t.outcome) for t in m.tokens],
        )
    log.info("polymarket.markets.summary", count=min(limit, len(markets)), next_cursor=cursor)
    return 0


async def _orderbook(token_id: str) -> int:
    settings = get_settings()
    configure_logging(settings)
    log = get_logger("meridian.polymarket.cli")
    try:
        async with PolymarketClient(settings) as client:
            book = await client.get_orderbook(token_id)
    except PolymarketHttpError as exc:
        log.error("polymarket.orderbook.failed", token_id=token_id, error=str(exc))
        return 1
    log.info(
        "polymarket.orderbook",
        token_id=token_id,
        bid_levels=len(book.bids),
        ask_levels=len(book.asks),
        best_bid=str(book.best_bid()) if book.best_bid() is not None else None,
        best_ask=str(book.best_ask()) if book.best_ask() is not None else None,
        spread=str(book.spread()) if book.spread() is not None else None,
        total_bid_size=str(book.total_bid_size()),
        total_ask_size=str(book.total_ask_size()),
    )
    return 0


async def _tap(asset_ids: list[str], seconds: int) -> int:
    settings = get_settings()
    configure_logging(settings)
    log = get_logger("meridian.polymarket.tap")
    log.info("tap.start", assets=asset_ids, seconds=seconds)

    deadline = time.monotonic() + seconds
    received = 0
    normalized = 0
    control = 0
    unknown_types: dict[str, int] = {}
    book_state = PolymarketBookState()

    async with PolymarketWebSocketClient(asset_ids=asset_ids) as client:
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
            events = normalize_polymarket_message(raw, book_state=book_state)
            if not events:
                msg_type = raw.get("event_type", "?")
                msg_type = msg_type if isinstance(msg_type, str) else "?"
                if msg_type in ("new_market", "market_resolved", "tick_size_change"):
                    control += 1
                else:
                    unknown_types[msg_type] = unknown_types.get(msg_type, 0) + 1
                continue
            normalized += len(events)
            for event in events:
                _log_event(log, event)

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
    from meridian.events import BookDeltaEvent, BookEvent, CanonicalEvent, TradeEvent

    assert isinstance(event, CanonicalEvent)
    payload = event.payload
    fields: dict[str, object] = {
        "asset_id": event.external_market_id,
        "seq": event.sequence_no,
        "kind": payload.kind.value,
    }
    if isinstance(payload, BookEvent):
        fields["levels"] = len(payload.levels)
        bids = [lv for lv in payload.levels if lv.side == "bid"]
        asks = [lv for lv in payload.levels if lv.side == "ask"]
        fields["best_bid"] = str(bids[0].price) if bids else None
        fields["best_ask"] = str(asks[0].price) if asks else None
    elif isinstance(payload, BookDeltaEvent):
        fields["side"] = payload.side
        fields["price"] = str(payload.price)
        fields["delta"] = str(payload.delta)
    elif isinstance(payload, TradeEvent):
        fields["price"] = str(payload.price)
        fields["size"] = str(payload.size)
        fields["aggressor"] = payload.aggressor
    # `log` is structlog's FilteringBoundLogger but typed as object to keep
    # the helper signature simple. The runtime call is correct.
    log.info("tap.event", **fields)  # type: ignore[attr-defined]
