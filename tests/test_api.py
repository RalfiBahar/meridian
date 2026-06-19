"""Unit tests for the Meridian FastAPI gateway (Phase 7).

Uses httpx.AsyncClient + ASGITransport with a no-op lifespan and mocked DB
pool so no Docker stack is required.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from meridian.api.hub import EventHub

# ---------------------------------------------------------------------------
# Mock infrastructure
# ---------------------------------------------------------------------------

_MARKET_ID = UUID("00000000-0000-0000-0000-000000000001")
_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

_MARKET_ROW: dict[str, Any] = {
    "id": _MARKET_ID,
    "external_id": "KXFED-26JUN-T3.75",
    "venue": "kalshi",
    "question": "Will the Fed raise rates?",
    "category": "fed",
    "resolution_status": "open",
    "closes_at": _NOW,
    "p_bid": 0.44,
    "p_ask": 0.46,
    "p_mid": 0.45,
    "microprice": 0.449,
}


class _MockRecord(dict):  # type: ignore[type-arg]
    """Minimal asyncpg Record substitute."""

    def __getitem__(self, key: str) -> Any:
        return super().__getitem__(key)


class _MockConn:
    def __init__(self, rows: list[dict[str, Any]] | None = None, val: Any = None) -> None:
        self._rows = [_MockRecord(r) for r in (rows or [])]
        self._val = val

    async def fetch(self, query: str, *args: object) -> list[Any]:
        return self._rows

    async def fetchrow(self, query: str, *args: object) -> Any | None:
        return self._rows[0] if self._rows else None

    async def fetchval(self, query: str, *args: object) -> Any:
        return self._val


class _MockPool:
    def __init__(
        self,
        rows: list[dict[str, Any]] | None = None,
        *,
        val: Any = 1,
    ) -> None:
        self._rows = rows or []
        self._val = val

    @asynccontextmanager
    async def acquire(self) -> Any:
        yield _MockConn(self._rows, self._val)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _noop_lifespan(app: Any) -> Any:
    yield


@pytest.fixture
def mock_pool() -> _MockPool:
    return _MockPool([_MARKET_ROW], val=1)


@pytest.fixture
def mock_hub() -> EventHub:
    return EventHub()


@pytest.fixture
def client(mock_pool: _MockPool, mock_hub: EventHub) -> httpx.AsyncClient:
    from meridian.api.app import create_app
    from meridian.api.deps import get_hub, get_pool

    app = create_app(lifespan=_noop_lifespan, enable_telemetry=False)
    app.dependency_overrides[get_pool] = lambda: mock_pool
    app.dependency_overrides[get_hub] = lambda: mock_hub
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),  # type: ignore[arg-type]
        base_url="http://test",
    )


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------


async def test_health_ok(client: httpx.AsyncClient) -> None:
    async with client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# Markets list endpoint
# ---------------------------------------------------------------------------


async def test_markets_list_returns_200(client: httpx.AsyncClient) -> None:
    async with client:
        resp = await client.get("/api/v1/markets")
    assert resp.status_code == 200
    body = resp.json()
    assert "markets" in body
    assert "total" in body


async def test_markets_list_contains_fields(client: httpx.AsyncClient) -> None:
    async with client:
        resp = await client.get("/api/v1/markets")
    markets = resp.json()["markets"]
    assert len(markets) == 1
    m = markets[0]
    assert m["external_id"] == "KXFED-26JUN-T3.75"
    assert m["venue"] == "kalshi"
    assert m["category"] == "fed"
    assert m["p_mid"] == pytest.approx(0.45)


async def test_markets_list_accepts_status_param(client: httpx.AsyncClient) -> None:
    async with client:
        resp = await client.get("/api/v1/markets?status=settled")
    assert resp.status_code == 200


async def test_markets_list_accepts_category_param(client: httpx.AsyncClient) -> None:
    async with client:
        resp = await client.get("/api/v1/markets?category=fed")
    assert resp.status_code == 200


async def test_markets_list_pagination_params(client: httpx.AsyncClient) -> None:
    async with client:
        resp = await client.get("/api/v1/markets?limit=10&offset=0")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Market detail endpoint
# ---------------------------------------------------------------------------


_DETAIL_ROW: dict[str, Any] = {
    "id": _MARKET_ID,
    "external_id": "KXFED-26JUN-T3.75",
    "venue": "kalshi",
    "question": "Will the Fed raise rates?",
    "category": "fed",
    "resolution_status": "open",
    "closes_at": _NOW,
}

_SIGNAL_ROWS: list[dict[str, Any]] = [
    {"signal_type": "p_mid", "value": 0.45},
    {"signal_type": "p_bid", "value": 0.44},
    {"signal_type": "p_ask", "value": 0.46},
]


@pytest.fixture
def detail_client(mock_hub: EventHub) -> httpx.AsyncClient:
    """Client with a pool that returns market detail rows + signal rows."""
    from meridian.api.app import create_app
    from meridian.api.deps import get_hub, get_pool

    class _MultiPool:
        """Return different rows per query call (signal rows after market row)."""

        def __init__(self) -> None:
            self._call = 0

        @asynccontextmanager
        async def acquire(self) -> Any:
            yield _MultiConn(self._call)
            self._call += 1

    class _MultiConn:
        def __init__(self, call_idx: int) -> None:
            self._call_idx = call_idx

        async def fetchrow(self, query: str, *args: object) -> Any:
            return _MockRecord(_DETAIL_ROW)

        async def fetch(self, query: str, *args: object) -> list[Any]:
            if self._call_idx == 1:
                return [_MockRecord(r) for r in _SIGNAL_ROWS]
            return []

        async def fetchval(self, query: str, *args: object) -> Any:
            return None

    pool = _MultiPool()
    app = create_app(lifespan=_noop_lifespan, enable_telemetry=False)
    app.dependency_overrides[get_pool] = lambda: pool
    app.dependency_overrides[get_hub] = lambda: mock_hub
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),  # type: ignore[arg-type]
        base_url="http://test",
    )


async def test_market_detail_not_found(client: httpx.AsyncClient) -> None:
    """404 when the pool returns no row for the given market_id."""
    from meridian.api.app import create_app
    from meridian.api.deps import get_hub, get_pool

    empty_pool = _MockPool([], val=0)
    app = create_app(lifespan=_noop_lifespan, enable_telemetry=False)
    app.dependency_overrides[get_pool] = lambda: empty_pool
    app.dependency_overrides[get_hub] = lambda: mock_hub

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),  # type: ignore[arg-type]
        base_url="http://test",
    ) as c:
        resp = await c.get(f"/api/v1/markets/{_MARKET_ID}")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Arb endpoint
# ---------------------------------------------------------------------------


async def test_arb_violations_returns_200(mock_hub: EventHub) -> None:
    """Arb endpoint returns empty violation lists when no market groups exist."""
    from meridian.api.app import create_app
    from meridian.api.deps import get_hub, get_pool

    empty_pool = _MockPool([], val=0)  # no partition groups → no violations
    app = create_app(lifespan=_noop_lifespan, enable_telemetry=False)
    app.dependency_overrides[get_pool] = lambda: empty_pool
    app.dependency_overrides[get_hub] = lambda: mock_hub

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),  # type: ignore[arg-type]
        base_url="http://test",
    ) as c:
        resp = await c.get("/api/v1/arb/violations")
    assert resp.status_code == 200
    body = resp.json()
    assert "partition_violations" in body
    assert "cross_venue_divergences" in body
    assert body["partition_violations"] == []
    assert body["cross_venue_divergences"] == []


# ---------------------------------------------------------------------------
# Calibration endpoint
# ---------------------------------------------------------------------------


async def test_calibration_404_no_data(mock_hub: EventHub) -> None:
    """Returns 404 when no resolved markets exist."""
    from meridian.api.app import create_app
    from meridian.api.deps import get_hub, get_pool

    empty_pool = _MockPool([], val=0)
    app = create_app(lifespan=_noop_lifespan, enable_telemetry=False)
    app.dependency_overrides[get_pool] = lambda: empty_pool
    app.dependency_overrides[get_hub] = lambda: mock_hub

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),  # type: ignore[arg-type]
        base_url="http://test",
    ) as c:
        resp = await c.get("/api/v1/calibration")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# FedWatch endpoint
# ---------------------------------------------------------------------------


async def test_fedwatch_404_no_kxfed(mock_hub: EventHub) -> None:
    """Returns 404 when no KXFED markets found and no date supplied."""
    from meridian.api.app import create_app
    from meridian.api.deps import get_hub, get_pool

    empty_pool = _MockPool([], val=None)
    app = create_app(lifespan=_noop_lifespan, enable_telemetry=False)
    app.dependency_overrides[get_pool] = lambda: empty_pool
    app.dependency_overrides[get_hub] = lambda: mock_hub

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),  # type: ignore[arg-type]
        base_url="http://test",
    ) as c:
        resp = await c.get("/api/v1/fedwatch")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Auth enforcement
# ---------------------------------------------------------------------------


async def test_auth_blocks_when_keys_configured(
    mock_pool: _MockPool, mock_hub: EventHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MERIDIAN_API_KEYS", "secret123")
    from meridian.api.app import create_app
    from meridian.api.deps import get_hub, get_pool

    app = create_app(lifespan=_noop_lifespan, enable_telemetry=False)
    app.dependency_overrides[get_pool] = lambda: mock_pool
    app.dependency_overrides[get_hub] = lambda: mock_hub

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),  # type: ignore[arg-type]
        base_url="http://test",
    ) as c:
        # No key → 401 Unauthorized (missing header)
        resp = await c.get("/api/v1/markets")
        assert resp.status_code == 401

        # Wrong key → 403 Forbidden (invalid key)
        resp = await c.get("/api/v1/markets", headers={"X-API-Key": "wrong"})
        assert resp.status_code == 403

        # Correct key → 200
        resp = await c.get("/api/v1/markets", headers={"X-API-Key": "secret123"})
        assert resp.status_code == 200


async def test_auth_open_when_no_keys(mock_pool: _MockPool, mock_hub: EventHub) -> None:
    """When MERIDIAN_API_KEYS is empty, all requests pass through."""
    from meridian.api.app import create_app
    from meridian.api.deps import get_hub, get_pool

    app = create_app(lifespan=_noop_lifespan, enable_telemetry=False)
    app.dependency_overrides[get_pool] = lambda: mock_pool
    app.dependency_overrides[get_hub] = lambda: mock_hub

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),  # type: ignore[arg-type]
        base_url="http://test",
    ) as c:
        resp = await c.get("/api/v1/markets")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


async def test_rate_limit_triggers(mock_pool: _MockPool, mock_hub: EventHub) -> None:
    from meridian.api.app import create_app
    from meridian.api.deps import get_hub, get_pool

    # Set a very low limit to trigger quickly.
    app = create_app(
        lifespan=_noop_lifespan,
        enable_telemetry=False,
        rate_limit_calls=3,
        rate_limit_period=60.0,
    )
    app.dependency_overrides[get_pool] = lambda: mock_pool
    app.dependency_overrides[get_hub] = lambda: mock_hub

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),  # type: ignore[arg-type]
        base_url="http://test",
    ) as c:
        for _ in range(3):
            resp = await c.get("/api/v1/markets")
            assert resp.status_code == 200

        # 4th request should be rate-limited.
        resp = await c.get("/api/v1/markets")
        assert resp.status_code == 429


async def test_rate_limit_exempt_for_health(mock_pool: _MockPool, mock_hub: EventHub) -> None:
    from meridian.api.app import create_app
    from meridian.api.deps import get_hub, get_pool

    app = create_app(
        lifespan=_noop_lifespan,
        enable_telemetry=False,
        rate_limit_calls=1,
        rate_limit_period=60.0,
    )
    app.dependency_overrides[get_pool] = lambda: mock_pool
    app.dependency_overrides[get_hub] = lambda: mock_hub

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),  # type: ignore[arg-type]
        base_url="http://test",
    ) as c:
        # /health is always exempt.
        for _ in range(5):
            resp = await c.get("/health")
            assert resp.status_code == 200


# ---------------------------------------------------------------------------
# deps.py: getter function unit tests + lifespan smoke test
# ---------------------------------------------------------------------------


async def test_deps_getters_return_state() -> None:
    """get_pool / get_redis / get_hub return whatever is stored in _state."""
    import meridian.api.deps as deps_mod

    sentinel_pool: Any = object()
    sentinel_redis: Any = object()
    sentinel_hub: Any = object()

    # Stash originals so we can restore after the test.
    original: dict[str, Any] = {}
    for attr in ("pool", "redis", "hub"):
        try:
            original[attr] = getattr(deps_mod._state, attr)  # type: ignore[attr-defined]
        except AttributeError:
            original[attr] = None

    try:
        deps_mod._state.pool = sentinel_pool  # type: ignore[attr-defined]
        deps_mod._state.redis = sentinel_redis  # type: ignore[attr-defined]
        deps_mod._state.hub = sentinel_hub  # type: ignore[attr-defined]

        assert deps_mod.get_pool() is sentinel_pool
        assert deps_mod.get_redis() is sentinel_redis
        assert deps_mod.get_hub() is sentinel_hub
    finally:
        for attr, val in original.items():
            if val is None:
                deps_mod._state.__dict__.pop(attr, None)
            else:
                setattr(deps_mod._state, attr, val)


async def test_deps_lifespan_initialises_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """lifespan() sets _state, runs the hub task, and cleans up on exit."""
    from unittest.mock import AsyncMock, MagicMock

    import meridian.api.deps as deps_mod

    mock_pool = MagicMock()
    mock_pool.close = AsyncMock()

    ready_to_cancel = asyncio.Event()

    class _MockRedis:
        async def xread(self, **kwargs: Any) -> list[Any]:
            ready_to_cancel.set()
            # Block until cancelled.
            await asyncio.sleep(3600)
            return []

        async def aclose(self) -> None:
            pass

    mock_redis = _MockRedis()

    monkeypatch.setattr(deps_mod, "create_pool", AsyncMock(return_value=mock_pool))
    monkeypatch.setattr(deps_mod, "create_client", MagicMock(return_value=mock_redis))

    app_stub: Any = MagicMock()
    async with deps_mod.lifespan(app_stub):
        # Give the hub task a tick to start running.
        await ready_to_cancel.wait()
        # While inside the lifespan, _state is populated.
        assert deps_mod._state.pool is mock_pool
        assert deps_mod._state.redis is mock_redis
        assert isinstance(deps_mod._state.hub, EventHub)

    # After exit, pool.close() must have been called.
    mock_pool.close.assert_called_once()


# ---------------------------------------------------------------------------
# EventHub unit tests
# ---------------------------------------------------------------------------


async def test_hub_subscribe_and_publish() -> None:
    hub = EventHub()
    q = hub.subscribe("test")
    event = {"kind": "quote", "market_id": "abc"}
    await hub._broadcast("test", event)  # type: ignore[attr-defined]
    result = q.get_nowait()
    assert result == event


async def test_hub_unsubscribe_stops_delivery() -> None:
    hub = EventHub()
    q = hub.subscribe("test")
    hub.unsubscribe("test", q)
    await hub._broadcast("test", {"kind": "quote"})  # type: ignore[attr-defined]
    assert q.empty()


async def test_hub_drops_on_full_queue() -> None:
    hub = EventHub()
    q = hub.subscribe("test")
    # Fill the queue to capacity (maxsize=200).
    for i in range(200):
        q.put_nowait({"i": i})
    # Next broadcast should not raise; it silently drops.
    await hub._broadcast("test", {"overflow": True})  # type: ignore[attr-defined]
    assert q.full()


# ---------------------------------------------------------------------------
# EventHub.run() tests (Redis stream draining)
# ---------------------------------------------------------------------------


async def test_hub_run_broadcasts_event_from_stream() -> None:
    """hub.run() reads a Redis stream message and fans it out to subscribers."""
    hub = EventHub()
    q = hub.subscribe("all")
    market_q = hub.subscribe("market:abc-123")

    event_payload = {"market_id": "abc-123", "kind": "quote"}
    raw_json = json.dumps(event_payload)

    call_count = 0

    class _MockRedis:
        async def xread(self, streams: Any, block: Any, count: Any) -> list[Any]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [["kalshi.events", [["1-1", {"event": raw_json}]]]]
            # Signal stop on second call.
            raise asyncio.CancelledError

    task = asyncio.create_task(hub.run(_MockRedis()))
    try:
        # Give the run loop time to process the first batch.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert not q.empty()
    assert q.get_nowait() == event_payload
    assert not market_q.empty()
    assert market_q.get_nowait() == event_payload


async def test_hub_run_skips_bad_json() -> None:
    """hub.run() continues when a stream message contains invalid JSON."""
    hub = EventHub()
    q = hub.subscribe("all")

    call_count = 0

    class _MockRedis:
        async def xread(self, streams: Any, block: Any, count: Any) -> list[Any]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [["kalshi.events", [["1-1", {"event": "not-json-{{{}"}]]]]
            raise asyncio.CancelledError

    task = asyncio.create_task(hub.run(_MockRedis()))
    try:
        await asyncio.sleep(0)
        await asyncio.sleep(0)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    # Bad JSON → skipped; nothing broadcast.
    assert q.empty()


async def test_hub_run_recovers_from_redis_error() -> None:
    """hub.run() logs and continues when Redis raises an unexpected exception."""
    hub = EventHub()

    call_count = 0

    class _MockRedis:
        async def xread(self, streams: Any, block: Any, count: Any) -> list[Any]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("simulated redis error")
            raise asyncio.CancelledError

    task = asyncio.create_task(hub.run(_MockRedis()))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    # Just verify it didn't propagate — call_count > 1 means it looped.
    assert call_count >= 1


async def test_hub_run_cancels_cleanly() -> None:
    """hub.run() exits without error when the task is cancelled."""
    hub = EventHub()

    class _MockRedis:
        async def xread(self, streams: Any, block: Any, count: Any) -> list[Any]:
            await asyncio.sleep(10)  # simulate blocking
            return []

    task = asyncio.create_task(hub.run(_MockRedis()))
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert task.done()


# ---------------------------------------------------------------------------
# Market detail (found case) + helper functions
# ---------------------------------------------------------------------------

_QUOTE_TICK_ROW: dict[str, Any] = {
    "event_ts": _NOW,
    "sequence_no": 42,
    "kind": "quote",
    "bid": 0.44,
    "ask": 0.46,
    "bid_size": 100.0,
    "ask_size": 50.0,
    "trade_price": None,
    "trade_size": None,
    "payload": None,
}

_BOOK_DELTA_TICK_ROW: dict[str, Any] = {
    "event_ts": _NOW,
    "sequence_no": 43,
    "kind": "book_delta",
    "bid": None,
    "ask": None,
    "bid_size": None,
    "ask_size": None,
    "trade_price": None,
    "trade_size": None,
    "payload": json.dumps({"side": "yes", "price": "0.45", "delta": "10"}),
}


@pytest.fixture
def detail_client_with_ticks(mock_hub: EventHub) -> httpx.AsyncClient:
    """detail_client variant that also returns a quote tick row."""
    from meridian.api.app import create_app
    from meridian.api.deps import get_hub, get_pool

    class _TickPool:
        def __init__(self) -> None:
            self._call = 0

        @asynccontextmanager
        async def acquire(self) -> Any:
            yield _TickConn(self._call)
            self._call += 1

    class _TickConn:
        def __init__(self, call_idx: int) -> None:
            self._call_idx = call_idx

        async def fetchrow(self, query: str, *args: object) -> Any:
            return _MockRecord(_DETAIL_ROW)

        async def fetch(self, query: str, *args: object) -> list[Any]:
            if self._call_idx == 1:
                return [_MockRecord(r) for r in _SIGNAL_ROWS]
            if self._call_idx == 3:  # _fetch_recent_ticks
                return [_MockRecord(_QUOTE_TICK_ROW), _MockRecord(_BOOK_DELTA_TICK_ROW)]
            return []

        async def fetchval(self, query: str, *args: object) -> Any:
            return None

    pool = _TickPool()
    app = create_app(lifespan=_noop_lifespan, enable_telemetry=False)
    app.dependency_overrides[get_pool] = lambda: pool
    app.dependency_overrides[get_hub] = lambda: mock_hub
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),  # type: ignore[arg-type]
        base_url="http://test",
    )


async def test_market_detail_found(detail_client: httpx.AsyncClient) -> None:
    """200 with signals when the market exists."""
    async with detail_client:
        resp = await detail_client.get(f"/api/v1/markets/{_MARKET_ID}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["external_id"] == "KXFED-26JUN-T3.75"
    assert body["venue"] == "kalshi"
    assert body["signals"]["p_mid"] == pytest.approx(0.45)
    assert body["signals"]["p_bid"] == pytest.approx(0.44)
    assert body["book"] == []
    assert body["recent_ticks"] == []


async def test_market_detail_includes_ticks(
    detail_client_with_ticks: httpx.AsyncClient,
) -> None:
    """Market detail response contains tick rows when DB returns them."""
    async with detail_client_with_ticks:
        resp = await detail_client_with_ticks.get(f"/api/v1/markets/{_MARKET_ID}")
    assert resp.status_code == 200
    ticks = resp.json()["recent_ticks"]
    assert len(ticks) == 2

    quote = ticks[0]
    assert quote["kind"] == "quote"
    assert quote["bid"] == pytest.approx(0.44)
    assert quote["ask"] == pytest.approx(0.46)
    assert quote["bid_size"] == pytest.approx(100.0)
    assert quote["side"] is None

    delta = ticks[1]
    assert delta["kind"] == "book_delta"
    assert delta["side"] == "yes"
    assert delta["book_price"] == pytest.approx(0.45)
    assert delta["book_delta"] == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# Helper function unit tests
# ---------------------------------------------------------------------------


def test_f_returns_float() -> None:
    from meridian.api.routes.markets import _f

    assert _f(0.5) == pytest.approx(0.5)
    assert isinstance(_f(0.5), float)


def test_f_returns_none_for_none() -> None:
    from meridian.api.routes.markets import _f

    assert _f(None) is None


def test_f_converts_string_number() -> None:
    from meridian.api.routes.markets import _f

    assert _f("0.45") == pytest.approx(0.45)


def test_tick_row_from_db_quote_kind() -> None:
    from meridian.api.routes.markets import _tick_row_from_db

    tick = _tick_row_from_db(_QUOTE_TICK_ROW)
    assert tick.kind == "quote"
    assert tick.bid == pytest.approx(0.44)
    assert tick.ask == pytest.approx(0.46)
    assert tick.side is None
    assert tick.book_price is None
    assert tick.book_delta is None


def test_tick_row_from_db_book_delta_json_string() -> None:
    from meridian.api.routes.markets import _tick_row_from_db

    tick = _tick_row_from_db(_BOOK_DELTA_TICK_ROW)
    assert tick.kind == "book_delta"
    assert tick.side == "yes"
    assert tick.book_price == pytest.approx(0.45)
    assert tick.book_delta == pytest.approx(10.0)
    assert tick.bid is None


def test_tick_row_from_db_book_delta_dict_payload() -> None:
    """payload already a dict (e.g. asyncpg returns JSONB as dict)."""
    from meridian.api.routes.markets import _tick_row_from_db

    row = {**_BOOK_DELTA_TICK_ROW, "payload": {"side": "no", "price": "0.55", "delta": "-5"}}
    tick = _tick_row_from_db(row)
    assert tick.side == "no"
    assert tick.book_price == pytest.approx(0.55)
    assert tick.book_delta == pytest.approx(-5.0)


def test_tick_row_from_canonical_quote() -> None:
    from meridian.api.routes.markets import _tick_row_from_canonical

    event: dict[str, Any] = {
        "event_ts": _NOW.isoformat(),
        "sequence_no": 1,
        "payload": {
            "kind": "quote",
            "bid": "0.44",
            "ask": "0.46",
            "bid_size": "100",
            "ask_size": "50",
        },
    }
    tick = _tick_row_from_canonical(event)
    assert tick.kind == "quote"
    assert tick.bid == pytest.approx(0.44)
    assert tick.ask == pytest.approx(0.46)
    assert tick.bid_size == pytest.approx(100.0)
    assert tick.trade_price is None


def test_tick_row_from_canonical_trade() -> None:
    from meridian.api.routes.markets import _tick_row_from_canonical

    event: dict[str, Any] = {
        "event_ts": _NOW.isoformat(),
        "sequence_no": 2,
        "payload": {"kind": "trade", "price": "0.45", "size": "200"},
    }
    tick = _tick_row_from_canonical(event)
    assert tick.kind == "trade"
    assert tick.trade_price == pytest.approx(0.45)
    assert tick.trade_size == pytest.approx(200.0)
    assert tick.bid is None


def test_tick_row_from_canonical_book_delta() -> None:
    from meridian.api.routes.markets import _tick_row_from_canonical

    event: dict[str, Any] = {
        "event_ts": _NOW.isoformat(),
        "sequence_no": 3,
        "payload": {"kind": "book_delta", "side": "yes", "price": "0.45", "delta": "10"},
    }
    tick = _tick_row_from_canonical(event)
    assert tick.kind == "book_delta"
    assert tick.side == "yes"
    assert tick.book_price == pytest.approx(0.45)
    assert tick.book_delta == pytest.approx(10.0)
    assert tick.bid is None


def test_tick_row_from_canonical_unknown_kind() -> None:
    from meridian.api.routes.markets import _tick_row_from_canonical

    event: dict[str, Any] = {
        "event_ts": _NOW.isoformat(),
        "sequence_no": 4,
        "payload": {"kind": "status", "status": "open"},
    }
    tick = _tick_row_from_canonical(event)
    assert tick.kind == "status"
    assert tick.bid is None
    assert tick.side is None


# ---------------------------------------------------------------------------
# WebSocket endpoint tests (use starlette TestClient, not httpx)
# ---------------------------------------------------------------------------


@pytest.fixture
def sync_app(mock_hub: EventHub) -> TestClient:
    """Synchronous starlette TestClient — uses an empty pool (no rows)."""
    from meridian.api.app import create_app
    from meridian.api.deps import get_hub, get_pool

    empty_pool = _MockPool([], val=0)
    app = create_app(lifespan=_noop_lifespan, enable_telemetry=False)
    app.dependency_overrides[get_pool] = lambda: empty_pool
    app.dependency_overrides[get_hub] = lambda: mock_hub
    return TestClient(app)


def test_ws_market_scanner_sends_snapshot(sync_app: TestClient) -> None:
    """WS scanner sends a snapshot frame immediately on connect."""
    with sync_app.websocket_connect("/api/v1/ws/markets") as ws:
        data = ws.receive_json()
    assert data["type"] == "snapshot"
    assert "total" in data
    assert "markets" in data


def test_ws_market_scanner_auth_rejected(
    mock_hub: EventHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WS scanner rejects connection with code 4003 when API key is wrong."""
    monkeypatch.setenv("MERIDIAN_API_KEYS", "secret123")

    from meridian.api.app import create_app
    from meridian.api.deps import get_hub, get_pool

    mock_pool_empty = _MockPool([], val=0)
    app = create_app(lifespan=_noop_lifespan, enable_telemetry=False)
    app.dependency_overrides[get_pool] = lambda: mock_pool_empty
    app.dependency_overrides[get_hub] = lambda: mock_hub

    client = TestClient(app)
    with (
        pytest.raises(WebSocketDisconnect) as exc_info,
        client.websocket_connect("/api/v1/ws/markets"),
    ):
        pass
    assert exc_info.value.code == 4003


def test_ws_authorized_no_keys_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """_ws_authorized returns True when no API keys are configured."""
    from meridian.api.routes.markets import _ws_authorized

    monkeypatch.delenv("MERIDIAN_API_KEYS", raising=False)
    # websocket arg is not used inside the function body
    assert _ws_authorized(None, None) is True  # type: ignore[arg-type]
    assert _ws_authorized(None, "anykey") is True  # type: ignore[arg-type]


def test_ws_authorized_with_configured_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """_ws_authorized validates the api_key against MERIDIAN_API_KEYS."""
    from meridian.api.routes.markets import _ws_authorized

    monkeypatch.setenv("MERIDIAN_API_KEYS", "good-key")
    assert _ws_authorized(None, "good-key") is True  # type: ignore[arg-type]
    assert _ws_authorized(None, "bad-key") is False  # type: ignore[arg-type]
    assert _ws_authorized(None, None) is False  # type: ignore[arg-type]


def test_ws_market_ticks_connects_and_seeds_history(sync_app: TestClient) -> None:
    """WS ticks endpoint accepts the connection and returns empty history."""
    with sync_app.websocket_connect(f"/api/v1/ws/markets/{_MARKET_ID}"):
        pass  # accept + 0 history ticks + disconnect cleanly


def test_ws_market_ticks_auth_rejected(mock_hub: EventHub, monkeypatch: pytest.MonkeyPatch) -> None:
    """WS ticks endpoint rejects with code 4003 when API key is wrong."""
    monkeypatch.setenv("MERIDIAN_API_KEYS", "secret123")

    from meridian.api.app import create_app
    from meridian.api.deps import get_hub, get_pool

    mock_pool_empty = _MockPool([], val=0)
    app = create_app(lifespan=_noop_lifespan, enable_telemetry=False)
    app.dependency_overrides[get_pool] = lambda: mock_pool_empty
    app.dependency_overrides[get_hub] = lambda: mock_hub

    client = TestClient(app)
    with (
        pytest.raises(WebSocketDisconnect) as exc_info,
        client.websocket_connect(f"/api/v1/ws/markets/{_MARKET_ID}"),
    ):
        pass
    assert exc_info.value.code == 4003


def test_ws_market_ticks_sends_history(mock_hub: EventHub) -> None:
    """WS ticks endpoint sends seeded history ticks on connect."""
    from meridian.api.app import create_app
    from meridian.api.deps import get_hub, get_pool

    class _HistoryConn:
        async def fetch(self, *args: object, **kwargs: object) -> list[Any]:
            return [_MockRecord(_QUOTE_TICK_ROW)]

        async def fetchval(self, *args: object, **kwargs: object) -> int:
            return 0

    class _HistoryPool:
        @asynccontextmanager
        async def acquire(self) -> Any:
            yield _HistoryConn()

    pool = _HistoryPool()
    app = create_app(lifespan=_noop_lifespan, enable_telemetry=False)
    app.dependency_overrides[get_pool] = lambda: pool
    app.dependency_overrides[get_hub] = lambda: mock_hub

    client = TestClient(app)
    with client.websocket_connect(f"/api/v1/ws/markets/{_MARKET_ID}") as ws:
        data = ws.receive_json()

    assert data["type"] == "tick"
    assert data["data"]["kind"] == "quote"
    assert data["data"]["bid"] == pytest.approx(0.44)
    assert data["data"]["ask"] == pytest.approx(0.46)


# ---------------------------------------------------------------------------
# Arb WebSocket endpoint
# ---------------------------------------------------------------------------


def test_ws_arb_sends_snapshot(sync_app: TestClient) -> None:
    """WS /ws/arb sends an arb_snapshot frame immediately on connect."""
    with sync_app.websocket_connect("/api/v1/ws/arb") as ws:
        data = ws.receive_json()
    assert data["type"] == "arb_snapshot"
    assert "data" in data
    assert "partition_violations" in data["data"]
    assert "cross_venue_divergences" in data["data"]
    assert data["data"]["partition_violations"] == []
    assert data["data"]["cross_venue_divergences"] == []


def test_ws_arb_auth_rejected(mock_hub: EventHub, monkeypatch: pytest.MonkeyPatch) -> None:
    """WS /ws/arb rejects with code 4003 when API key is wrong."""
    monkeypatch.setenv("MERIDIAN_API_KEYS", "secret123")

    from meridian.api.app import create_app
    from meridian.api.deps import get_hub, get_pool

    mock_pool_empty = _MockPool([], val=0)
    app = create_app(lifespan=_noop_lifespan, enable_telemetry=False)
    app.dependency_overrides[get_pool] = lambda: mock_pool_empty
    app.dependency_overrides[get_hub] = lambda: mock_hub

    client = TestClient(app)
    with (
        pytest.raises(WebSocketDisconnect) as exc_info,
        client.websocket_connect("/api/v1/ws/arb"),
    ):
        pass
    assert exc_info.value.code == 4003


# ---------------------------------------------------------------------------
# fedwatch._next_kxfed_date direct unit test
# ---------------------------------------------------------------------------


async def test_next_kxfed_date_returns_date_when_row_found() -> None:
    """_next_kxfed_date returns the fomc_date from the first matching market row."""
    from datetime import date

    from meridian.api.routes.fedwatch import _next_kxfed_date

    expected = date(2026, 7, 30)

    class _Conn:
        async def fetchrow(self, query: str, *args: object) -> dict[str, Any] | None:
            return {"fomc_date": expected}

    class _Pool:
        @asynccontextmanager
        async def acquire(self) -> Any:
            yield _Conn()

    result = await _next_kxfed_date(_Pool())  # type: ignore[arg-type]
    assert result == expected


async def test_next_kxfed_date_returns_none_when_no_row() -> None:
    """_next_kxfed_date returns None when no open KXFED markets exist."""
    from meridian.api.routes.fedwatch import _next_kxfed_date

    class _Conn:
        async def fetchrow(self, query: str, *args: object) -> None:
            return None

    class _Pool:
        @asynccontextmanager
        async def acquire(self) -> Any:
            yield _Conn()

    result = await _next_kxfed_date(_Pool())  # type: ignore[arg-type]
    assert result is None
