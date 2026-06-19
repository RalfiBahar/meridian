"""Unit tests for the shared run_with_reconnect reconnect loop.

Targets the uncovered branches in reconnect.py:
- _drain_stream swallows ConnectionClosedOK (line 138)
- _run_connection with no stop_event (lines 106-111)
- _run_connection propagates drain exception (line 130)
- _run_connection CancelledError propagation (line 65, 116-119)
- run_with_reconnect ConnectionClosedOK + stop_event set (lines 69-75)
- run_with_reconnect ConnectionClosedOK reconnect path (lines 71-75)
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import MagicMock

import pytest
from websockets.exceptions import ConnectionClosedOK

from meridian.ingest.reconnect import (
    _drain_stream,
    _run_connection,
    run_with_reconnect,
)
from meridian.ingest.stats import IngestStats


class _FakeClient:
    """Fake stream client that yields a sequence then optionally raises."""

    def __init__(
        self,
        messages: list[dict[str, Any]],
        then_raise: Exception | None = None,
    ) -> None:
        self._messages = messages
        self._then_raise = then_raise

    async def stream(self) -> AsyncIterator[dict[str, Any]]:
        for msg in self._messages:
            yield msg
        if self._then_raise is not None:
            raise self._then_raise


def _fake_log() -> Any:
    """Return a structlog-compatible mock logger."""
    log = MagicMock()
    log.warning = MagicMock()
    log.info = MagicMock()
    return log


# ---------------------------------------------------------------------------
# _drain_stream tests
# ---------------------------------------------------------------------------


async def test_drain_stream_yields_all_messages() -> None:
    received: list[dict[str, Any]] = []

    async def handle(raw: dict[str, Any]) -> None:
        received.append(raw)

    client = _FakeClient([{"a": 1}, {"b": 2}])
    await _drain_stream(client, handle)
    assert received == [{"a": 1}, {"b": 2}]


async def test_drain_stream_swallows_connection_closed_ok() -> None:
    """ConnectionClosedOK during iteration is silently discarded (line 138)."""
    received: list[dict[str, Any]] = []

    async def handle(raw: dict[str, Any]) -> None:
        received.append(raw)

    client = _FakeClient([{"a": 1}], then_raise=ConnectionClosedOK(None, None))
    await _drain_stream(client, handle)
    assert received == [{"a": 1}]


async def test_drain_stream_propagates_other_exceptions() -> None:
    async def handle(raw: dict[str, Any]) -> None:
        pass

    client = _FakeClient([], then_raise=ValueError("unexpected"))
    with pytest.raises(ValueError, match="unexpected"):
        await _drain_stream(client, handle)


# ---------------------------------------------------------------------------
# _run_connection tests
# ---------------------------------------------------------------------------


async def test_run_connection_no_stop_event_drains_fully() -> None:
    """stop_event=None: drain runs to completion and returns normally (lines 106-111)."""
    received: list[dict[str, Any]] = []

    async def handle(raw: dict[str, Any]) -> None:
        received.append(raw)

    client = _FakeClient([{"x": 1}, {"x": 2}])

    @asynccontextmanager
    async def connect() -> AsyncIterator[_FakeClient]:
        yield client

    await _run_connection(connect, handle, stop_event=None)
    assert len(received) == 2


async def test_run_connection_propagates_drain_exception() -> None:
    """If the drain task raises, _run_connection re-raises it (line 130)."""

    async def handle(raw: dict[str, Any]) -> None:
        pass

    client = _FakeClient([], then_raise=RuntimeError("drain boom"))

    @asynccontextmanager
    async def connect() -> AsyncIterator[_FakeClient]:
        yield client

    with pytest.raises(RuntimeError, match="drain boom"):
        await _run_connection(connect, handle, stop_event=asyncio.Event())


async def test_run_connection_stop_event_exits_cleanly() -> None:
    """stop_event fires while draining — _run_connection returns without error."""
    stop = asyncio.Event()

    async def handle(raw: dict[str, Any]) -> None:
        stop.set()  # trigger stop mid-stream

    client = _FakeClient([{"m": 1}, {"m": 2}, {"m": 3}])

    @asynccontextmanager
    async def connect() -> AsyncIterator[_FakeClient]:
        yield client

    await _run_connection(connect, handle, stop_event=stop)


async def test_run_connection_no_stop_event_cancelled_error_propagates() -> None:
    """CancelledError propagates when stop_event is None (lines 108-110)."""
    started = asyncio.Event()

    class _InfiniteClient:
        async def stream(self) -> AsyncIterator[dict[str, Any]]:
            while True:
                started.set()
                await asyncio.sleep(0.01)
                yield {"m": 1}

    @asynccontextmanager
    async def connect() -> AsyncIterator[_InfiniteClient]:
        yield _InfiniteClient()

    async def handle(raw: dict[str, Any]) -> None:
        pass

    task = asyncio.create_task(_run_connection(connect, handle, stop_event=None))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ---------------------------------------------------------------------------
# run_with_reconnect tests
# ---------------------------------------------------------------------------


async def test_run_with_reconnect_cancelled_error_propagates() -> None:
    """CancelledError from _run_connection is re-raised (line 65)."""
    started = asyncio.Event()

    class _InfiniteClient:
        async def stream(self) -> AsyncIterator[dict[str, Any]]:
            while True:
                started.set()
                await asyncio.sleep(0.01)
                yield {"m": 1}

    @asynccontextmanager
    async def connect() -> AsyncIterator[_InfiniteClient]:
        yield _InfiniteClient()

    async def handle(raw: dict[str, Any]) -> None:
        pass

    stats = IngestStats()
    task = asyncio.create_task(
        run_with_reconnect(
            connect=connect,
            handle_one=handle,
            stats=stats,
            log=_fake_log(),
        )
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_run_with_reconnect_connection_closed_ok_stop_set_returns() -> None:
    """ConnectionClosedOK + stop_event already set → return immediately (lines 69-70)."""
    stop = asyncio.Event()
    stop.set()

    @asynccontextmanager
    async def connect() -> AsyncIterator[Any]:
        raise ConnectionClosedOK(None, None)
        yield  # unreachable but required for the generator type

    async def handle(raw: dict[str, Any]) -> None:
        pass

    stats = IngestStats()
    await run_with_reconnect(
        connect=connect,
        handle_one=handle,
        stats=stats,
        log=_fake_log(),
        stop_event=stop,
    )
    assert stats.reconnects == 0


async def test_run_with_reconnect_connection_closed_ok_reconnects_then_stops() -> None:
    """ConnectionClosedOK without stop → reconnect; stop_event set on second try (lines 71-75)."""
    attempt_count = 0
    stop = asyncio.Event()

    @asynccontextmanager
    async def connect() -> AsyncIterator[Any]:
        nonlocal attempt_count
        attempt_count += 1
        if attempt_count >= 2:
            stop.set()
        raise ConnectionClosedOK(None, None)
        yield  # unreachable

    async def handle(raw: dict[str, Any]) -> None:
        pass

    reconnect_calls: list[int] = []

    def on_reconnect() -> None:
        reconnect_calls.append(1)

    stats = IngestStats()
    await run_with_reconnect(
        connect=connect,
        handle_one=handle,
        stats=stats,
        log=_fake_log(),
        stop_event=stop,
        on_reconnect=on_reconnect,
    )
    assert attempt_count >= 2
    assert stats.reconnects >= 1
    assert len(reconnect_calls) >= 1


async def test_run_with_reconnect_cancelled_in_asyncio_wait() -> None:
    """CancelledError during asyncio.wait inside _run_connection propagates (lines 116-119)."""
    in_drain = asyncio.Event()

    class _SlowClient:
        async def stream(self) -> AsyncIterator[dict[str, Any]]:
            in_drain.set()
            await asyncio.sleep(10)
            yield {"m": 1}

    @asynccontextmanager
    async def connect() -> AsyncIterator[_SlowClient]:
        yield _SlowClient()

    async def handle(raw: dict[str, Any]) -> None:
        pass

    stats = IngestStats()
    stop = asyncio.Event()
    task = asyncio.create_task(
        run_with_reconnect(
            connect=connect,
            handle_one=handle,
            stats=stats,
            log=_fake_log(),
            stop_event=stop,
        )
    )
    await in_drain.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_run_with_reconnect_stop_event_checked_at_loop_start() -> None:
    """Pre-set stop_event → run_with_reconnect returns immediately without connecting."""
    stop = asyncio.Event()
    stop.set()

    connect_calls = 0

    @asynccontextmanager
    async def connect() -> AsyncIterator[Any]:
        nonlocal connect_calls
        connect_calls += 1
        yield _FakeClient([])

    async def handle(raw: dict[str, Any]) -> None:
        pass

    stats = IngestStats()
    await run_with_reconnect(
        connect=connect,
        handle_one=handle,
        stats=stats,
        log=_fake_log(),
        stop_event=stop,
    )
    assert connect_calls == 0
