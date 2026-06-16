"""Unit tests for PolymarketIngestWorker reconnect behaviour.

Mirrors `test_ingest_worker.py`'s structure (local WS server + mock DB pool,
no Docker required).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
import websockets
from websockets.asyncio.server import serve as ws_serve

from meridian.config import Settings
from meridian.ingest.polymarket_worker import PolymarketIngestWorker

# ---------------------------------------------------------------------------
# Lightweight DB-pool stub (same shape as test_ingest_worker.py's)
# ---------------------------------------------------------------------------


class _MockConn:
    async def fetchrow(self, query: str, *args: object) -> dict[str, Any]:
        return {"id": 1}

    async def execute(self, query: str, *args: object) -> str:
        return "INSERT 0 1"

    async def executemany(self, query: str, records: object) -> None:
        pass


class _MockPool:
    @asynccontextmanager
    async def acquire(self) -> Any:
        yield _MockConn()


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _book_msg(asset_id: str, idx: int) -> str:
    return json.dumps(
        [
            {
                "event_type": "book",
                "asset_id": asset_id,
                "market": "0xmkt",
                "timestamp": str(1_000_000 + idx),
                "bids": [{"price": "0.50", "size": "10"}],
                "asks": [{"price": "0.52", "size": "20"}],
            }
        ]
    )


async def _drain_client_subscribe(ws: Any) -> None:
    with contextlib.suppress(Exception):
        await asyncio.wait_for(ws.recv(), timeout=2.0)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_worker_reconnects_after_server_close(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Worker re-connects when the server closes the WS connection cleanly."""
    MESSAGES_PER_CONNECTION = 3
    connect_count = 0
    connected_twice = asyncio.Event()

    async def handler(ws: Any) -> None:
        nonlocal connect_count
        connect_count += 1
        if connect_count >= 2:
            connected_twice.set()
        await _drain_client_subscribe(ws)
        for i in range(MESSAGES_PER_CONNECTION):
            await ws.send(_book_msg("RECONNECT-TEST", i))
        # Handler exits -> server closes connection -> worker reconnects.

    stop_event = asyncio.Event()

    async with ws_serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        monkeypatch.setattr("meridian.polymarket.ws.WS_URL", f"ws://127.0.0.1:{port}")

        worker = PolymarketIngestWorker(
            settings,
            _MockPool(),
            asset_ids=["RECONNECT-TEST"],
        )

        async def _stopper() -> None:
            await connected_twice.wait()
            stop_event.set()

        stopper_task = asyncio.create_task(_stopper())
        stats = await worker.run(stop_event=stop_event)
        stopper_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await stopper_task

    assert connect_count >= 2, "expected at least one reconnect"
    assert stats.reconnects >= 1, "reconnect counter should be >= 1"
    assert stats.received >= MESSAGES_PER_CONNECTION, "should have received messages"


async def test_worker_stops_on_event(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    """Setting the stop_event causes run() to return promptly."""

    async def handler(ws: Any) -> None:
        await _drain_client_subscribe(ws)
        idx = 0
        try:
            while True:
                await asyncio.sleep(0.01)
                await ws.send(_book_msg("STOP-TEST", idx))
                idx += 1
        except Exception:
            pass

    stop_event = asyncio.Event()

    async with ws_serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        monkeypatch.setattr("meridian.polymarket.ws.WS_URL", f"ws://127.0.0.1:{port}")

        worker = PolymarketIngestWorker(
            settings,
            _MockPool(),
            asset_ids=["STOP-TEST"],
        )

        async def _stopper() -> None:
            await asyncio.sleep(0.05)
            stop_event.set()

        stopper_task = asyncio.create_task(_stopper())
        stats = await worker.run(stop_event=stop_event)
        with contextlib.suppress(asyncio.CancelledError):
            await stopper_task

    assert stats.received >= 1


async def test_worker_publish_to_redis(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    """Persisted events are XADDed to the Redis stream when redis is provided."""

    async def handler(ws: Any) -> None:
        await _drain_client_subscribe(ws)
        await ws.send(_book_msg("REDIS-TEST", 0))

    stop_event = asyncio.Event()

    async with ws_serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        monkeypatch.setattr("meridian.polymarket.ws.WS_URL", f"ws://127.0.0.1:{port}")

        mock_redis = AsyncMock()
        mock_redis.xadd = AsyncMock(return_value=b"1-0")

        worker = PolymarketIngestWorker(
            settings,
            _MockPool(),
            asset_ids=["REDIS-TEST"],
            redis=mock_redis,
        )

        async def _stopper() -> None:
            await asyncio.sleep(0.15)
            stop_event.set()

        stopper_task = asyncio.create_task(_stopper())
        stats = await worker.run(stop_event=stop_event)
        with contextlib.suppress(asyncio.CancelledError):
            await stopper_task

    assert mock_redis.xadd.called, "xadd should have been called"
    assert stats.events_published >= 1


async def test_worker_backoff_on_connection_error(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Worker backs off when the server is unavailable."""
    connect_attempts = 0
    attempted_thrice = asyncio.Event()

    async def fake_connect(*args: object, **kwargs: object) -> None:
        nonlocal connect_attempts
        connect_attempts += 1
        if connect_attempts >= 3:
            attempted_thrice.set()
        raise OSError("connection refused")

    monkeypatch.setattr(websockets, "connect", fake_connect)
    monkeypatch.setattr("meridian.polymarket.ws.WS_URL", "ws://127.0.0.1:1")
    monkeypatch.setattr("meridian.ingest.reconnect._BACKOFF_INITIAL", 0.01)
    monkeypatch.setattr("meridian.ingest.reconnect._BACKOFF_MAX", 0.05)

    stop_event = asyncio.Event()
    worker = PolymarketIngestWorker(
        settings,
        _MockPool(),
        asset_ids=["ERR-TEST"],
    )

    async def _stopper() -> None:
        await attempted_thrice.wait()
        stop_event.set()

    stopper_task = asyncio.create_task(_stopper())
    stats = await worker.run(stop_event=stop_event)
    stopper_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await stopper_task

    assert connect_attempts >= 3
    assert stats.reconnects >= 2


async def test_enrich_market_updates_db(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """_enrich_market calls get_market and updates the DB row."""
    from meridian.polymarket.models import PolymarketMarket, PolymarketToken

    executed_sqls: list[str] = []

    class _EnrichConn(_MockConn):
        async def execute(self, query: str, *args: object) -> str:
            executed_sqls.append(query)
            return "UPDATE 1"

    class _EnrichPool(_MockPool):
        @asynccontextmanager
        async def acquire(self) -> Any:
            yield _EnrichConn()

    mock_market = PolymarketMarket(
        condition_id="0xmkt",
        question="Will it rain?",
        tokens=[PolymarketToken(token_id="ENRICH-TEST", outcome="Yes")],
    )

    monkeypatch.setattr(
        "meridian.polymarket.client.PolymarketClient.get_market",
        AsyncMock(return_value=mock_market),
    )

    worker = PolymarketIngestWorker(
        settings,
        _EnrichPool(),
        asset_ids=["ENRICH-TEST"],
    )

    await worker._enrich_market(
        "ENRICH-TEST", UUID("00000000-0000-0000-0000-000000000001"), "0xmkt"
    )

    assert any("UPDATE" in sql for sql in executed_sqls), "expected an UPDATE statement"
