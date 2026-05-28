"""Kalshi environment-aware URL helpers.

The signed request path is `REST_PATH + relative_path`, where `relative_path`
starts with a leading slash (e.g. `/markets`). The full URL is
`REST_HOST[env] + REST_PATH + relative_path`.
"""

from __future__ import annotations

from meridian.config import KalshiEnv

REST_HOST: dict[KalshiEnv, str] = {
    "demo": "https://demo-api.kalshi.co",
    "prod": "https://api.elections.kalshi.com",
}

WS_HOST: dict[KalshiEnv, str] = {
    "demo": "wss://demo-api.kalshi.co",
    "prod": "wss://api.elections.kalshi.com",
}

REST_PATH = "/trade-api/v2"
WS_PATH = "/trade-api/ws/v2"


def rest_base(env: KalshiEnv) -> str:
    """Return the REST base URL including the `/trade-api/v2` segment."""
    return REST_HOST[env] + REST_PATH


def ws_url(env: KalshiEnv) -> str:
    """Return the full WebSocket URL."""
    return WS_HOST[env] + WS_PATH


def signed_path(relative_path: str) -> str:
    """Build the path used in the request signature payload."""
    if not relative_path.startswith("/"):
        raise ValueError(f"relative_path must start with '/': {relative_path!r}")
    return REST_PATH + relative_path
