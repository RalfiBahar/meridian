"""API key authentication middleware.

API keys are read from the `MERIDIAN_API_KEYS` environment variable as a
comma-separated list.  An empty list disables auth (useful in development).

Keys are passed in the `X-API-Key` header.  The `/health` endpoint is always
public (bypasses auth).
"""

from __future__ import annotations

import os

from fastapi import HTTPException, Security, status
from fastapi.security.api_key import APIKeyHeader

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

_PUBLIC_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}


def _valid_keys() -> frozenset[str]:
    raw = os.environ.get("MERIDIAN_API_KEYS", "")
    return frozenset(k.strip() for k in raw.split(",") if k.strip())


def get_valid_keys() -> frozenset[str]:
    """Return the set of configured API keys (empty = open access)."""
    return _valid_keys()


async def require_api_key(api_key: str | None = Security(_api_key_header)) -> str:
    """FastAPI dependency — enforce API key auth.

    Returns the validated key on success.  Raises 401/403 on failure.
    Skip by adding to `_PUBLIC_PATHS` or by leaving MERIDIAN_API_KEYS empty.
    """
    valid = _valid_keys()
    if not valid:
        return "dev-mode"  # no keys configured → open access

    if api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-API-Key header",
        )
    if api_key not in valid:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key",
        )
    return api_key
