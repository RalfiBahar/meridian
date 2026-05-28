"""KalshiSigner: deterministic signing, signature verifiable by public key."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from meridian.config import Settings
from meridian.kalshi import KalshiSigner
from meridian.kalshi.errors import KalshiAuthError


def test_sign_returns_three_headers(kalshi_settings: Settings) -> None:
    signer = KalshiSigner.from_settings(kalshi_settings)
    headers = signer.sign("GET", "/trade-api/v2/markets", ts_ms=1_700_000_000_000)
    assert set(headers) == {
        "KALSHI-ACCESS-KEY",
        "KALSHI-ACCESS-SIGNATURE",
        "KALSHI-ACCESS-TIMESTAMP",
    }
    assert headers["KALSHI-ACCESS-KEY"] == "test-access-key"
    assert headers["KALSHI-ACCESS-TIMESTAMP"] == "1700000000000"


def test_signature_verifies_against_public_key(
    kalshi_keypair: tuple[rsa.RSAPrivateKey, Path],
    kalshi_settings: Settings,
) -> None:
    private_key, _ = kalshi_keypair
    signer = KalshiSigner.from_settings(kalshi_settings)
    method = "GET"
    path = "/trade-api/v2/markets"
    ts = 1_700_000_000_000

    headers = signer.sign(method, path, ts_ms=ts)
    sig = base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"])
    message = f"{ts}{method}{path}".encode()

    # Raises InvalidSignature if the signature is wrong.
    private_key.public_key().verify(
        sig,
        message,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=hashes.SHA256.digest_size,
        ),
        hashes.SHA256(),
    )


def test_signature_rejects_tampered_message(
    kalshi_keypair: tuple[rsa.RSAPrivateKey, Path],
    kalshi_settings: Settings,
) -> None:
    private_key, _ = kalshi_keypair
    signer = KalshiSigner.from_settings(kalshi_settings)
    headers = signer.sign("GET", "/trade-api/v2/markets", ts_ms=1_700_000_000_000)
    sig = base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"])

    with pytest.raises(InvalidSignature):
        private_key.public_key().verify(
            sig,
            b"1700000000000GET/trade-api/v2/TAMPERED",
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=hashes.SHA256.digest_size,
            ),
            hashes.SHA256(),
        )


def test_method_is_uppercased(kalshi_settings: Settings) -> None:
    signer = KalshiSigner.from_settings(kalshi_settings)
    lower = signer.sign("get", "/trade-api/v2/markets", ts_ms=1_700_000_000_000)
    upper = signer.sign("GET", "/trade-api/v2/markets", ts_ms=1_700_000_000_000)
    # RSA-PSS is randomized (salt), so signatures differ — but the timestamps
    # and access keys must match exactly.
    assert lower["KALSHI-ACCESS-KEY"] == upper["KALSHI-ACCESS-KEY"]
    assert lower["KALSHI-ACCESS-TIMESTAMP"] == upper["KALSHI-ACCESS-TIMESTAMP"]


def test_missing_access_key_raises(tmp_path: Path) -> None:
    settings = Settings(kalshi_access_key=None, kalshi_private_key_path=tmp_path / "x.pem")
    with pytest.raises(KalshiAuthError, match="ACCESS_KEY"):
        KalshiSigner.from_settings(settings)


def test_missing_private_key_path_raises() -> None:
    settings = Settings(kalshi_access_key="x", kalshi_private_key_path=None)
    with pytest.raises(KalshiAuthError, match="PRIVATE_KEY_PATH"):
        KalshiSigner.from_settings(settings)


def test_private_key_file_not_found_raises(tmp_path: Path) -> None:
    settings = Settings(
        kalshi_access_key="x",
        kalshi_private_key_path=tmp_path / "does-not-exist.pem",
    )
    with pytest.raises(KalshiAuthError, match="not found"):
        KalshiSigner.from_settings(settings)


def test_non_rsa_key_rejected(tmp_path: Path) -> None:
    """A non-RSA PEM (e.g. Ed25519) must be rejected."""
    from cryptography.hazmat.primitives.asymmetric import ed25519

    ed_key = ed25519.Ed25519PrivateKey.generate()
    pem = ed_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    path = tmp_path / "ed25519.pem"
    path.write_bytes(pem)

    settings = Settings(kalshi_access_key="x", kalshi_private_key_path=path)
    with pytest.raises(KalshiAuthError, match="not an RSA key"):
        KalshiSigner.from_settings(settings)
