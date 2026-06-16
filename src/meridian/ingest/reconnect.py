"""Shared reconnect-with-backoff loop for long-running ingest workers.

`KalshiIngestWorker` and `PolymarketIngestWorker` both need the same finite
state machine: connect, drain messages until the connection drops or
`stop_event` fires, then reconnect — immediately on a clean server close
(WS code 1000), with exponential backoff (+jitter) on any other error.
Extracted here so each venue-specific worker only supplies `connect()` (a
callable returning a fresh, not-yet-entered async context manager whose
`__aenter__` yields something with `.stream()`) and `handle_one(raw)`.
"""

from __future__ import annotations

import asyncio
import contextlib
import random
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol

from structlog.typing import FilteringBoundLogger
from websockets.exceptions import ConnectionClosedOK

from meridian.ingest.stats import IngestStats

_BACKOFF_INITIAL = 1.0
_BACKOFF_MAX = 60.0
_BACKOFF_JITTER = 0.20


class StreamClient(Protocol):
    def stream(self) -> AsyncIterator[dict[str, Any]]: ...


ConnectFn = Callable[[], AbstractAsyncContextManager[StreamClient]]
HandleFn = Callable[[dict[str, Any]], Awaitable[None]]


async def _wait_for_event(event: asyncio.Event) -> None:
    await event.wait()


async def run_with_reconnect(
    *,
    connect: ConnectFn,
    handle_one: HandleFn,
    stats: IngestStats,
    log: FilteringBoundLogger,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Run indefinitely, reconnecting with exponential backoff on error.

    Returns when `stop_event` is set (or the task is cancelled). On a clean
    WS close from the server, reconnects immediately and resets the backoff
    delay. Increments `stats.reconnects` on every reconnect.
    """
    delay = _BACKOFF_INITIAL
    while True:
        if stop_event is not None and stop_event.is_set():
            return
        try:
            await _run_connection(connect, handle_one, stop_event)
        except asyncio.CancelledError:
            raise
        except ConnectionClosedOK:
            # Server closed cleanly (WS code 1000) before we could complete
            # the subscribe or read messages — reconnect at once.
            if stop_event is not None and stop_event.is_set():
                return
            stats.reconnects += 1
            delay = _BACKOFF_INITIAL
            continue
        except Exception as exc:
            log.warning("ingest.reconnecting", error=str(exc), delay=round(delay, 2))
            jitter = delay * random.uniform(-_BACKOFF_JITTER, _BACKOFF_JITTER)
            await asyncio.sleep(delay + jitter)
            delay = min(delay * 2, _BACKOFF_MAX)
            stats.reconnects += 1
            continue

        # _run_connection returned normally: either stop_event fired or the
        # server closed the connection cleanly.
        if stop_event is not None and stop_event.is_set():
            return
        # Server-initiated close -> reconnect immediately, reset backoff.
        stats.reconnects += 1
        delay = _BACKOFF_INITIAL


async def _run_connection(
    connect: ConnectFn,
    handle_one: HandleFn,
    stop_event: asyncio.Event | None,
) -> None:
    async with connect() as client:
        drain: asyncio.Task[None] = asyncio.create_task(_drain_stream(client, handle_one))

        if stop_event is None:
            try:
                await drain
            except asyncio.CancelledError:
                drain.cancel()
                raise
            return

        stop: asyncio.Task[None] = asyncio.create_task(_wait_for_event(stop_event))
        try:
            await asyncio.wait({drain, stop}, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            drain.cancel()
            stop.cancel()
            raise

        for task in (drain, stop):
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        if drain.done() and not drain.cancelled():
            exc = drain.exception()
            if exc is not None:
                raise exc


async def _drain_stream(client: StreamClient, handle_one: HandleFn) -> None:
    try:
        async for raw in client.stream():
            await handle_one(raw)
    except ConnectionClosedOK:
        pass  # server closed cleanly; run_with_reconnect() will reconnect
