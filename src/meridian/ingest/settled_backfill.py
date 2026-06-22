"""Sync actually settled Kalshi markets + candlestick p_mid history for calibration."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import asyncpg

from meridian.kalshi.client import KalshiClient
from meridian.kalshi.models import KalshiMarket
from meridian.kalshi.normalize import kalshi_market_id

# Frontend calibration tabs -> Kalshi series tickers with settled history.
SERIES_BY_CATEGORY: dict[str, list[str]] = {
    "fed": ["KXFED"],
    "econ": ["KXCPI", "KXUNRATE", "KXGDP", "KXPCE"],
    "politics": ["KXPRES", "KXSEN", "KXHOUSE", "KXGOV"],
    "crypto": ["KXBTC", "KXETH"],
    "sports": ["KXNBA", "KXMLB", "KXNFL", "KXNHL"],
}


@dataclass
class SettledBackfillResult:
    """Summary of one sync-settled run."""

    markets_upserted: int = 0
    signals_written: int = 0
    synthetic_removed: int = 0
    by_category: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"Markets upserted: {self.markets_upserted}",
            f"p_mid signals written: {self.signals_written}",
            f"Synthetic rows removed: {self.synthetic_removed}",
        ]
        if self.by_category:
            lines.append("By category: " + ", ".join(f"{k}={v}" for k, v in sorted(self.by_category.items())))
        if self.errors:
            lines.append(f"Errors ({len(self.errors)}): " + "; ".join(self.errors[:3]))
        return "\n".join(lines)


def series_ticker_for(market_ticker: str) -> str:
    """KXFED-26JUN-T5.25 -> KXFED."""
    return market_ticker.split("-", 1)[0]


def settled_value_from_market(raw: dict[str, Any] | KalshiMarket) -> int | None:
    """Map Kalshi settlement to 0/1 for markets.settled_value."""
    if isinstance(raw, KalshiMarket):
        result = (raw.result or "").lower()
        settlement = raw.settlement_value_dollars
        last = raw.last_price
    else:
        result = str(raw.get("result") or "").lower()
        settlement = raw.get("settlement_value_dollars")
        last = raw.get("last_price_dollars")

    if result == "yes":
        return 1
    if result == "no":
        return 0
    if settlement is not None:
        return 1 if float(settlement) >= 0.5 else 0
    if last is not None:
        return 1 if float(last) >= 0.5 else 0
    return None


def p_mid_from_candlestick(candle: dict[str, Any]) -> float | None:
    """Mid-price from one Kalshi candlestick row."""
    yes_bid = candle.get("yes_bid") or {}
    yes_ask = candle.get("yes_ask") or {}
    bid = yes_bid.get("close_dollars")
    ask = yes_ask.get("close_dollars")
    if bid is not None and ask is not None:
        b, a = float(bid), float(ask)
        if b > 0 and a > 0 and a >= b:
            return max(0.01, min(0.99, (b + a) / 2))

    price = candle.get("price") or {}
    for key in ("mean_dollars", "close_dollars", "previous_dollars", "open_dollars"):
        val = price.get(key)
        if val is not None:
            p = float(val)
            if 0.0 <= p <= 1.0:
                return max(0.01, min(0.99, p))
    return None


def _parse_dt(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _ts(dt: datetime | str | None) -> int | None:
    parsed = _parse_dt(dt)
    if parsed is None:
        return None
    return int(parsed.timestamp())


async def remove_synthetic_settled(pool: asyncpg.Pool) -> int:
    """Delete demo/non-Kalshi settled rows before a live sync."""
    async with pool.acquire() as conn:
        ids = await conn.fetch(
            """
            SELECT id FROM markets
            WHERE resolution_status = 'settled'
              AND COALESCE(metadata->>'source', '') <> 'kalshi_settled_sync'
            """
        )
        if not ids:
            return 0
        market_ids = [r["id"] for r in ids]
        await conn.execute(
            "DELETE FROM signals WHERE market_id = ANY($1::uuid[])",
            market_ids,
        )
        result = await conn.execute(
            "DELETE FROM markets WHERE id = ANY($1::uuid[])",
            market_ids,
        )
    return int(result.split()[-1])


async def _upsert_settled_market(
    pool: asyncpg.Pool,
    *,
    venue_id: int,
    category: str,
    raw: dict[str, Any],
) -> UUID | None:
    ticker = raw["ticker"]
    outcome = settled_value_from_market(raw)
    if outcome is None:
        return None

    market_id = kalshi_market_id(ticker)
    opens_at = _parse_dt(raw.get("open_time"))
    closes_at = _parse_dt(raw.get("close_time"))
    settled_at = _parse_dt(raw.get("settlement_ts")) or closes_at
    metadata = {
        "source": "kalshi_settled_sync",
        "result": raw.get("result"),
        "event_ticker": raw.get("event_ticker"),
        "settlement_value_dollars": str(raw.get("settlement_value_dollars") or ""),
    }

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO markets (
                id, venue_id, external_id, ticker, question, category,
                resolution_status, settled_value,
                opens_at, closes_at, settled_at, metadata, created_at, updated_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, 'settled', $7, $8, $9, $10, $11::jsonb, now(), now())
            ON CONFLICT (venue_id, external_id) DO UPDATE SET
                ticker            = EXCLUDED.ticker,
                question          = EXCLUDED.question,
                category          = EXCLUDED.category,
                resolution_status = 'settled',
                settled_value     = EXCLUDED.settled_value,
                opens_at          = EXCLUDED.opens_at,
                closes_at         = EXCLUDED.closes_at,
                settled_at        = EXCLUDED.settled_at,
                metadata          = EXCLUDED.metadata,
                updated_at        = now()
            """,
            market_id,
            venue_id,
            ticker,
            ticker,
            raw.get("title") or ticker,
            category,
            outcome,
            opens_at,
            closes_at,
            settled_at,
            json.dumps(metadata),
        )
    return market_id


async def _write_candlestick_signals(
    pool: asyncpg.Pool,
    *,
    market_id: UUID,
    candles: list[dict[str, Any]],
    period_interval: int,
) -> int:
    rows: list[tuple[Any, ...]] = []
    meta = json.dumps({"source": "kalshi_candlesticks", "period_interval": period_interval})
    for candle in candles:
        p = p_mid_from_candlestick(candle)
        end_ts = candle.get("end_period_ts")
        if p is None or end_ts is None:
            continue
        event_ts = datetime.fromtimestamp(int(end_ts), tz=UTC)
        rows.append((event_ts, market_id, "p_mid", p, meta, event_ts))

    if not rows:
        return 0

    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM signals WHERE market_id = $1 AND signal_type = 'p_mid'",
            market_id,
        )
        await conn.executemany(
            """
            INSERT INTO signals (event_ts, market_id, signal_type, value, metadata, ingest_ts)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6)
            """,
            rows,
        )
    return len(rows)


async def sync_settled_markets(
    pool: asyncpg.Pool,
    client: KalshiClient,
    *,
    categories: list[str] | None = None,
    max_per_series: int = 100,
    period_interval: int = 1440,
    remove_synthetic: bool = True,
) -> SettledBackfillResult:
    """Fetch settled Kalshi markets and write real calibration history."""
    result = SettledBackfillResult()
    if remove_synthetic:
        result.synthetic_removed = await remove_synthetic_settled(pool)

    venue_id = await pool.fetchval("SELECT id FROM venues WHERE code = 'kalshi'")
    if venue_id is None:
        result.errors.append("kalshi venue row missing")
        return result

    target_categories = categories or list(SERIES_BY_CATEGORY.keys())

    for category in target_categories:
        series_list = SERIES_BY_CATEGORY.get(category, [])
        cat_count = 0
        for series in series_list:
            try:
                markets_raw = await client.list_markets_all(
                    status="settled",
                    series_ticker=series,
                    max_pages=max(1, (max_per_series + 199) // 200),
                )
            except Exception as exc:
                result.errors.append(f"{series}: {exc}")
                continue

            for raw_market in markets_raw[:max_per_series]:
                payload = raw_market.model_dump(mode="json", by_alias=True)
                ticker = payload.get("ticker")
                if not ticker:
                    continue

                outcome = settled_value_from_market(payload)
                if outcome is None:
                    continue

                try:
                    market_id = await _upsert_settled_market(
                        pool,
                        venue_id=int(venue_id),
                        category=category,
                        raw=payload,
                    )
                    if market_id is None:
                        continue

                    open_ts = _ts(raw_market.open_time)
                    end_ts = _ts(raw_market.settlement_ts or raw_market.close_time)
                    if open_ts is None or end_ts is None:
                        result.errors.append(f"{ticker}: missing open/close timestamps")
                        continue

                    candles = await client.get_candlesticks(
                        series,
                        ticker,
                        start_ts=open_ts,
                        end_ts=end_ts,
                        period_interval=period_interval,
                    )
                    n_sig = await _write_candlestick_signals(
                        pool,
                        market_id=market_id,
                        candles=candles,
                        period_interval=period_interval,
                    )
                    if n_sig == 0:
                        # Last-resort: one snapshot from settlement-time quote fields.
                        bid = payload.get("previous_yes_bid_dollars")
                        ask = payload.get("previous_yes_ask_dollars")
                        if bid and ask:
                            p = max(0.01, min(0.99, (float(bid) + float(ask)) / 2))
                            settle_ts = datetime.fromtimestamp(end_ts, tz=UTC)
                            async with pool.acquire() as conn:
                                await conn.execute(
                                    """
                                    INSERT INTO signals (event_ts, market_id, signal_type, value, metadata, ingest_ts)
                                    VALUES ($1, $2, 'p_mid', $3, $4::jsonb, $1)
                                    """,
                                    settle_ts,
                                    market_id,
                                    p,
                                    json.dumps({"source": "kalshi_settlement_snapshot"}),
                                )
                            n_sig = 1

                    result.markets_upserted += 1
                    result.signals_written += n_sig
                    cat_count += 1
                except Exception as exc:
                    result.errors.append(f"{ticker}: {exc}")

        result.by_category[category] = cat_count

    return result
