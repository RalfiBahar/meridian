"""Unit tests for the Meridian FastAPI gateway (Phase 7).

Uses httpx.AsyncClient + ASGITransport with a no-op lifespan and mocked DB
pool so no Docker stack is required.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
import pytest

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
