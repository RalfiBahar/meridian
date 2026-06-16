"""Polymarket CLOB endpoint constants.

Unlike Kalshi, there is a single production deployment — no demo/prod
`Literal` to route on. See `docs/polymarket.md`.
"""

from __future__ import annotations

REST_BASE = "https://clob.polymarket.com"
WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

# Client must send this literal text frame roughly every 10s or the server
# disconnects. See docs/polymarket.md#heartbeat.
PING_INTERVAL_SECONDS = 10.0
PING_TEXT = "PING"
