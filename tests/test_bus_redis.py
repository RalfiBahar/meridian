"""Unit tests for meridian.bus.redis — client factory."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from meridian.bus.redis import client_context, create_client
from meridian.config import Settings


def test_create_client_returns_redis_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    """create_client returns an aioredis.Redis without opening a connection."""
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    client = create_client(s)
    assert client is not None


async def test_client_context_yields_and_closes() -> None:
    """client_context yields the client and calls aclose() on exit."""
    mock_client = MagicMock()
    mock_client.aclose = AsyncMock()

    with patch("meridian.bus.redis.create_client", return_value=mock_client):
        from meridian.config import Settings as _S

        s = _S(_env_file=None)  # type: ignore[call-arg]
        async with client_context(s) as c:
            assert c is mock_client

    mock_client.aclose.assert_awaited_once()
