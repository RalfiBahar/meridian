"""Shared pytest fixtures."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from meridian.config import Settings


@pytest.fixture
def settings() -> Iterator[Settings]:
    """Settings instance for tests. Honors .env and process env."""
    yield Settings()


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
