"""Kalshi request signing: RSA-PSS over `{ts_ms}{METHOD}{signed_path}`."""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from meridian.config import Settings
from meridian.kalshi.errors import KalshiAuthError


@dataclass(frozen=True)
class KalshiSigner:
    """Produces the three Kalshi auth headers for a single request.

    The signed payload is `f"{ts_ms}{METHOD_UPPER}{signed_path}".encode()`,
    signed with RSA-PSS (MGF1-SHA256, salt_length=SHA256.digest_size, SHA256),
    then base64-encoded.
    """

    access_key: str
    private_key: rsa.RSAPrivateKey

    @classmethod
    def from_settings(cls, settings: Settings) -> KalshiSigner:
        if not settings.kalshi_access_key:
            raise KalshiAuthError(
                "MERIDIAN_KALSHI_ACCESS_KEY is not set; cannot sign Kalshi requests."
            )
        if settings.kalshi_private_key_path is None:
            raise KalshiAuthError(
                "MERIDIAN_KALSHI_PRIVATE_KEY_PATH is not set; cannot sign Kalshi requests."
            )
        path = settings.kalshi_private_key_path.expanduser()
        if not path.is_file():
            raise KalshiAuthError(f"Kalshi private-key file not found: {path}")
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        if not isinstance(key, rsa.RSAPrivateKey):
            raise KalshiAuthError(
                f"Kalshi private-key file is not an RSA key: {path} ({type(key).__name__})"
            )
        return cls(access_key=settings.kalshi_access_key, private_key=key)

    def sign(
        self,
        method: str,
        signed_path: str,
        *,
        ts_ms: int | None = None,
    ) -> dict[str, str]:
        if ts_ms is None:
            ts_ms = int(time.time() * 1000)
        message = f"{ts_ms}{method.upper()}{signed_path}".encode()
        signature = self.private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=hashes.SHA256.digest_size,
            ),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self.access_key,
            "KALSHI-ACCESS-TIMESTAMP": str(ts_ms),
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("ascii"),
        }
