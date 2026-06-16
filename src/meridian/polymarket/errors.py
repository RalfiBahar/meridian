"""Polymarket client exception hierarchy."""

from __future__ import annotations


class PolymarketError(Exception):
    """Base for Polymarket client errors."""


class PolymarketHttpError(PolymarketError):
    """The CLOB API returned a non-2xx HTTP status."""

    def __init__(self, *, status: int, method: str, path: str, body: str) -> None:
        truncated = body[:200] + ("..." if len(body) > 200 else "")
        super().__init__(f"Polymarket {method} {path} returned {status}: {truncated}")
        self.status = status
        self.method = method
        self.path = path
        self.body = body
