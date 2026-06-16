"""Polymarket CLOB API client.

Exports the public surface:

- `PolymarketClient`  — async REST client (context-managed)
- `PolymarketMarket`, `PolymarketOrderbook` — typed responses
- `PolymarketError`, `PolymarketHttpError` — exception hierarchy
- `PolymarketWebSocketClient` — market-data WS client
- `normalize_polymarket_message`, `polymarket_market_id`, `PolymarketBookState`
- `REST_BASE`, `WS_URL` — endpoint constants

Unlike `meridian.kalshi`, there is no signer to export: Polymarket's
read-only market-data surface requires no authentication. See
`docs/polymarket.md`.
"""

from __future__ import annotations

from meridian.polymarket.client import PolymarketClient
from meridian.polymarket.endpoints import REST_BASE, WS_URL
from meridian.polymarket.errors import PolymarketError, PolymarketHttpError
from meridian.polymarket.models import PolymarketMarket, PolymarketOrderbook
from meridian.polymarket.normalize import (
    PolymarketBookState,
    normalize_polymarket_message,
    polymarket_market_id,
)
from meridian.polymarket.ws import PolymarketWebSocketClient

__all__ = [
    "REST_BASE",
    "WS_URL",
    "PolymarketBookState",
    "PolymarketClient",
    "PolymarketError",
    "PolymarketHttpError",
    "PolymarketMarket",
    "PolymarketOrderbook",
    "PolymarketWebSocketClient",
    "normalize_polymarket_message",
    "polymarket_market_id",
]
