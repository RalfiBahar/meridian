"""FastAPI application factory for the Meridian quant terminal gateway (Phase 7).

Entry point: `meridian serve` (see `cli/serve.py`) or
  uvicorn meridian.api.app:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from meridian.api.deps import lifespan as _default_lifespan
from meridian.api.routes import arb, calibration, fedwatch, health, markets
from meridian.api.telemetry import configure_telemetry, instrument_fastapi

# ---------------------------------------------------------------------------
# Rate limiting middleware
# ---------------------------------------------------------------------------

_PUBLIC_PATHS = frozenset({"/health", "/docs", "/openapi.json", "/redoc"})


class _Limiter:
    """Simple sliding-window rate limiter (in-process; not distributed)."""

    def __init__(self, calls: int, period: float) -> None:
        self._calls = calls
        self._period = period
        self._hits: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self._period
        hits = [t for t in self._hits[key] if t > cutoff]
        self._hits[key] = hits
        if len(hits) >= self._calls:
            return False
        hits.append(now)
        return True


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Enforce per-key (or per-IP) rate limits on non-public routes."""

    def __init__(
        self,
        app: Any,
        *,
        calls: int = 120,
        period: float = 60.0,
    ) -> None:
        super().__init__(app)
        self._limiter = _Limiter(calls=calls, period=period)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.url.path in _PUBLIC_PATHS:
            return await call_next(request)

        key = request.headers.get("X-API-Key") or (
            request.client.host if request.client else "unknown"
        )
        if not self._limiter.is_allowed(key):
            return Response(
                content='{"detail":"Rate limit exceeded"}',
                status_code=429,
                media_type="application/json",
            )
        return await call_next(request)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app(
    *,
    lifespan: Any = None,
    enable_telemetry: bool = True,
    rate_limit_calls: int = 120,
    rate_limit_period: float = 60.0,
) -> FastAPI:
    """Create and configure the FastAPI application.

    Parameters
    ----------
    lifespan:
        Custom lifespan context manager. Defaults to the standard one that
        opens the DB pool, Redis client, and EventHub. Override in tests.
    enable_telemetry:
        Set to False to skip OpenTelemetry setup (e.g. in unit tests).
    rate_limit_calls / rate_limit_period:
        Sliding-window rate-limit parameters.
    """
    if enable_telemetry:
        configure_telemetry()

    app = FastAPI(
        title="Meridian API",
        description=(
            "Prediction-market research engine — REST + WebSocket gateway "
            "for the quant terminal frontend."
        ),
        version="7.0.0",
        lifespan=lifespan or _default_lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(
        RateLimitMiddleware,
        calls=rate_limit_calls,
        period=rate_limit_period,
    )

    app.include_router(health.router)
    app.include_router(markets.router, prefix="/api/v1")
    app.include_router(arb.router, prefix="/api/v1")
    app.include_router(calibration.router, prefix="/api/v1")
    app.include_router(fedwatch.router, prefix="/api/v1")

    if enable_telemetry:
        instrument_fastapi(app)

    return app


# Module-level singleton consumed by uvicorn.
app = create_app()
