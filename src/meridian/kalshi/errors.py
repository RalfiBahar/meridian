"""Kalshi client exception hierarchy."""

from __future__ import annotations


class KalshiError(Exception):
    """Base for Kalshi client errors."""


class KalshiAuthError(KalshiError):
    """Misconfigured or missing credentials."""


class KalshiHttpError(KalshiError):
    """The exchange returned a non-2xx HTTP status."""

    def __init__(self, *, status: int, method: str, path: str, body: str) -> None:
        truncated = body[:200] + ("..." if len(body) > 200 else "")
        super().__init__(f"Kalshi {method} {path} returned {status}: {truncated}")
        self.status = status
        self.method = method
        self.path = path
        self.body = body
