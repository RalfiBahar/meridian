"""Kalshi exchange API client.

Exports the public surface:

- `KalshiClient`     — async REST client (context-managed)
- `KalshiSigner`     — RSA-PSS request signer
- `KalshiMarket`,    `KalshiOrderbook`, `KalshiMarketStatus` — typed responses
- `KalshiError`,     `KalshiAuthError`, `KalshiHttpError` — exception hierarchy
- `rest_base`,       `ws_url` — environment-aware URL helpers
"""

from __future__ import annotations

from meridian.kalshi.auth import KalshiSigner
from meridian.kalshi.client import KalshiClient
from meridian.kalshi.endpoints import rest_base, ws_url
from meridian.kalshi.errors import KalshiAuthError, KalshiError, KalshiHttpError
from meridian.kalshi.models import KalshiMarket, KalshiMarketStatus, KalshiOrderbook
from meridian.kalshi.normalize import kalshi_market_id, normalize_kalshi_message
from meridian.kalshi.ws import KalshiWebSocketClient

__all__ = [
    "KalshiAuthError",
    "KalshiClient",
    "KalshiError",
    "KalshiHttpError",
    "KalshiMarket",
    "KalshiMarketStatus",
    "KalshiOrderbook",
    "KalshiSigner",
    "KalshiWebSocketClient",
    "kalshi_market_id",
    "normalize_kalshi_message",
    "rest_base",
    "ws_url",
]
