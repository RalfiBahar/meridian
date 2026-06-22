"""Discover high-volume Kalshi + Polymarket markets for live ingest."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import httpx

from meridian.kalshi.client import KalshiClient
from meridian.kalshi.models import KalshiMarket

# Series to scan: (series_ticker, max_markets, mode)
# mode "event" = all strikes from the highest-volume event (Fed arb partitions)
# mode "top"   = top N by volume across the series
KALSHI_SERIES: list[tuple[str, int, str]] = [
    ("KXFED", 12, "event"),
    ("KXCPI", 4, "top"),
    ("KXBTC", 3, "top"),
    ("KXHIGHNY", 3, "top"),
    ("KXNBA", 4, "top"),
]

MAX_KALSHI_TICKERS = 25
MAX_POLYMARKET_ASSETS = 6
GAMMA_API = "https://gamma-api.polymarket.com/markets"

# Fallback when API discovery fails (offline / no credentials).
DEFAULT_KALSHI_TICKERS = (
    "KXFED-27APR-T4.25,KXFED-27APR-T4.00,KXFED-27APR-T3.75,"
    "KXFED-27APR-T3.50,KXFED-27APR-T3.25,KXFED-27APR-T3.00,"
    "KXHIGHNY-26JUN19-B83.5,KXHIGHNY-26JUN19-B85.5,KXHIGHNY-26JUN19-B87.5"
)
DEFAULT_POLYMARKET_ASSETS = (
    "88275040060084773376557187972215267513049848642895776801789297917961077894224,"
    "94376205816022955542979635542279932967359915765455578534002478996104754801969,"
    "21695138873211375451055566770107682325494206727818897067665810321709249824909"
)


@dataclass
class IngestDiscovery:
    """Tickers/assets chosen for live ingest workers."""

    kalshi_tickers: list[str] = field(default_factory=list)
    polymarket_assets: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def kalshi_csv(self) -> str:
        return ",".join(self.kalshi_tickers) if self.kalshi_tickers else DEFAULT_KALSHI_TICKERS

    @property
    def polymarket_csv(self) -> str:
        return (
            ",".join(self.polymarket_assets)
            if self.polymarket_assets
            else DEFAULT_POLYMARKET_ASSETS
        )

    def summary(self) -> str:
        lines = [
            f"Kalshi tickers ({len(self.kalshi_tickers)}): {', '.join(self.kalshi_tickers[:5])}"
            + (" …" if len(self.kalshi_tickers) > 5 else ""),
            f"Polymarket assets ({len(self.polymarket_assets)}): "
            f"{len(self.polymarket_assets)} token IDs",
        ]
        if self.errors:
            lines.append(f"Warnings: {'; '.join(self.errors[:3])}")
        return "\n".join(lines)


def _volume(m: KalshiMarket) -> float:
    try:
        return float(m.volume or 0)
    except (TypeError, ValueError):
        return 0.0


def _pick_event_strikes(markets: list[KalshiMarket], limit: int) -> list[str]:
    """All strikes from the open event with highest combined volume."""
    by_event: dict[str, list[KalshiMarket]] = defaultdict(list)
    for m in markets:
        event = m.event_ticker or m.ticker.rsplit("-", 1)[0]
        by_event[event].append(m)
    if not by_event:
        return []
    best_event = max(by_event, key=lambda e: sum(_volume(m) for m in by_event[e]))
    chosen = sorted(by_event[best_event], key=_volume, reverse=True)
    return [m.ticker for m in chosen[:limit]]


def _pick_top_volume(markets: list[KalshiMarket], limit: int) -> list[str]:
    ranked = sorted(markets, key=_volume, reverse=True)
    return [m.ticker for m in ranked[:limit]]


async def _fetch_open_series(client: KalshiClient, series: str) -> list[KalshiMarket]:
    return await client.list_markets_all(status="open", series_ticker=series, max_pages=3)


async def discover_kalshi_tickers(client: KalshiClient | None) -> tuple[list[str], list[str]]:
    """Return (tickers, errors). Uses authenticated client when available."""
    errors: list[str] = []
    chosen: list[str] = []
    seen: set[str] = set()

    if client is None:
        return [], ["Kalshi client unavailable — using defaults"]

    for series, limit, mode in KALSHI_SERIES:
        try:
            markets = await _fetch_open_series(client, series)
        except Exception as exc:
            errors.append(f"{series}: {exc}")
            continue
        if not markets:
            errors.append(f"{series}: no open markets")
            continue

        if mode == "event":
            tickers = _pick_event_strikes(markets, limit)
        else:
            tickers = _pick_top_volume(markets, limit)

        for t in tickers:
            if t not in seen:
                seen.add(t)
                chosen.append(t)
            if len(chosen) >= MAX_KALSHI_TICKERS:
                break
        if len(chosen) >= MAX_KALSHI_TICKERS:
            break

    return chosen, errors


async def discover_polymarket_assets() -> tuple[list[str], list[str]]:
    """Top Polymarket Yes-token IDs by volume via the public gamma API."""
    errors: list[str] = []
    assets: list[str] = []

    try:
        async with httpx.AsyncClient(timeout=15.0) as http:
            resp = await http.get(
                GAMMA_API,
                params={
                    "active": "true",
                    "closed": "false",
                    "limit": 50,
                    "order": "volume",
                    "ascending": "false",
                },
            )
            resp.raise_for_status()
            rows: list[dict[str, Any]] = resp.json()
    except Exception as exc:
        return [], [f"gamma API: {exc}"]

    ranked: list[tuple[float, str, str]] = []
    for row in rows:
        if not row.get("enableOrderBook") or not row.get("acceptingOrders"):
            continue
        try:
            vol = float(row.get("volumeNum") or row.get("volume") or 0)
        except (TypeError, ValueError):
            vol = 0.0
        raw_ids = row.get("clobTokenIds")
        if not raw_ids:
            continue
        try:
            token_ids = json.loads(raw_ids) if isinstance(raw_ids, str) else raw_ids
        except json.JSONDecodeError:
            continue
        if not token_ids:
            continue
        # Subscribe to the Yes outcome (first token) for each popular market.
        ranked.append((vol, str(token_ids[0]), str(row.get("question", ""))[:60]))

    ranked.sort(key=lambda x: x[0], reverse=True)
    seen: set[str] = set()
    for _vol, token_id, _q in ranked:
        if token_id in seen:
            continue
        seen.add(token_id)
        assets.append(token_id)
        if len(assets) >= MAX_POLYMARKET_ASSETS:
            break

    if not assets:
        errors.append("gamma API returned no orderbook-enabled markets")
    return assets, errors


async def discover_ingest_markets(
    kalshi_client: KalshiClient | None,
) -> IngestDiscovery:
    """Discover popular markets for both venues."""
    result = IngestDiscovery()

    tickers, k_errs = await discover_kalshi_tickers(kalshi_client)
    result.kalshi_tickers = tickers
    result.errors.extend(k_errs)

    assets, p_errs = await discover_polymarket_assets()
    result.polymarket_assets = assets
    result.errors.extend(p_errs)

    return result


def write_ingest_env(path: str, discovery: IngestDiscovery) -> None:
    """Write KALSHI_INGEST_TICKERS / POLYMARKET_INGEST_ASSETS to a dotenv file."""
    from pathlib import Path

    content = (
        "# Auto-generated by: uv run python -m meridian.cli markets discover\n"
        "# Re-run setup or `meridian markets discover` to refresh popular markets.\n"
        f"KALSHI_INGEST_TICKERS={discovery.kalshi_csv}\n"
        f"POLYMARKET_INGEST_ASSETS={discovery.polymarket_csv}\n"
    )
    Path(path).write_text(content, encoding="utf-8")


def update_dotenv_keys(env_path: str, discovery: IngestDiscovery) -> None:
    """Merge ingest ticker lines into the project .env file."""
    from pathlib import Path

    path = Path(env_path)
    lines: list[str] = []
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()

    updates = {
        "KALSHI_INGEST_TICKERS": discovery.kalshi_csv,
        "POLYMARKET_INGEST_ASSETS": discovery.polymarket_csv,
    }
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)
    for key, val in updates.items():
        if key not in seen:
            out.append(f"{key}={val}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
