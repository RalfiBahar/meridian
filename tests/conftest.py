"""Shared pytest fixtures."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from meridian.config import Settings


@pytest.fixture
def settings() -> Iterator[Settings]:
    """Settings instance for tests. Honors .env and process env."""
    yield Settings()


# ---------------------------------------------------------------------------
# Mock asyncpg pool fixtures
# ---------------------------------------------------------------------------


class MockConn:
    """Minimal asyncpg Connection stand-in for unit tests.

    Records every execute / executemany call so tests can assert on the
    queries and parameters that were sent without a real database.
    """

    def __init__(self) -> None:
        self.execute_result = "INSERT 0 1"
        self.fetchrow_result: dict[str, Any] | None = {"id": 1}
        self.executions: list[tuple[str, tuple[object, ...]]] = []
        self.executemany_calls: list[tuple[str, list[Any]]] = []

    async def fetchrow(self, query: str, *args: object) -> dict[str, Any] | None:
        return self.fetchrow_result

    async def execute(self, query: str, *args: object) -> str:
        self.executions.append((query, args))
        return self.execute_result

    async def executemany(self, query: str, records: object) -> None:
        self.executemany_calls.append(
            (query, list(records) if hasattr(records, "__iter__") else [records])
        )


class MockPool:
    """Minimal asyncpg Pool stand-in for unit tests."""

    def __init__(self, conn: MockConn | None = None) -> None:
        self.conn = conn if conn is not None else MockConn()

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[MockConn]:
        yield self.conn


@pytest.fixture
def mock_conn() -> MockConn:
    """A configurable mock asyncpg connection."""
    return MockConn()


@pytest.fixture
def mock_pool(mock_conn: MockConn) -> MockPool:
    """A mock asyncpg pool wrapping a `mock_conn` instance.

    Tests can inspect `mock_pool.conn.executions` or mutate
    `mock_pool.conn.execute_result` / `.fetchrow_result` to simulate
    different DB responses.
    """
    return MockPool(mock_conn)


@pytest.fixture
def kalshi_keypair(tmp_path: Path) -> tuple[rsa.RSAPrivateKey, Path]:
    """Generate a throwaway 2048-bit RSA keypair and write it to a temp PEM file."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    key_path = tmp_path / "test-kalshi-key.pem"
    key_path.write_bytes(pem)
    return private_key, key_path


@pytest.fixture
def kalshi_settings(kalshi_keypair: tuple[rsa.RSAPrivateKey, Path]) -> Settings:
    """Settings populated with a valid throwaway Kalshi keypair."""
    _, key_path = kalshi_keypair
    return Settings(
        kalshi_env="demo",
        kalshi_access_key="test-access-key",
        kalshi_private_key_path=key_path,
    )
