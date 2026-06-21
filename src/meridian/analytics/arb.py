"""No-arbitrage consistency engine (Phase 3).

Two types of violations are detected and written to the `signals` table:

  arb_violation_bps   — within-partition price inconsistency:
                         sum(ask_i) < 1 → long arb (buy all contracts)
                         sum(bid_i) > 1 → short arb (sell all contracts)
                         Severity in basis points = dollar profit per $1 face
                         value of the trade, scaled to bps.

  cross_venue_divergence_bps — price divergence between a Kalshi market and
                                a Polymarket market linked via
                                market_groups(type='cross_venue').

Kalshi standard maker/taker fee: 2 cents per contract (0.02 USD), deducted
from winnings.  The fee-adjusted spread is `spread + 2 * FEE_PER_SIDE`.

`check_partition_arb` uses `scipy.optimize.linprog` to solve the feasibility
LP for the general case (where spreads are asymmetric across contracts), not
just the trivial sum check — though for mutually-exclusive binary partitions
the LP reduces to exactly the sum check.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import asyncpg
import numpy as np
from scipy.optimize import linprog

# Kalshi standard fee per contract side (USD, approximately 2¢).
KALSHI_FEE_PER_SIDE: Decimal = Decimal("0.02")


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class PartitionArbResult:
    group_id: UUID
    group_label: str
    n_contracts: int
    min_ask_sum: Decimal  # sum(best asks)  — < 1 → long arb
    max_bid_sum: Decimal  # sum(best bids)  — > 1 → short arb
    violation_bps: Decimal  # 0 if no arb, > 0 if arb exists
    direction: str  # "long" | "short" | "none"
    depth_feasible: bool
    market_ids: list[UUID]


@dataclass
class ArbAggregateStats:
    """Aggregate statistics over historical arb signals."""

    lookback_days: int
    total_violations: int
    violations_per_day: float
    median_severity_bps: float | None


@dataclass
class CrossVenueArbResult:
    group_id: UUID
    market_id_a: UUID
    venue_a: str
    p_mid_a: Decimal
    market_id_b: UUID
    venue_b: str
    p_mid_b: Decimal
    divergence_bps: Decimal


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def run_partition_monitor(
    pool: asyncpg.Pool,
    *,
    threshold_bps: Decimal = Decimal("0"),
    write_signals: bool = True,
) -> list[PartitionArbResult]:
    """Check every 'partition' market_group for no-arb violations.

    Returns a list of `PartitionArbResult` with `violation_bps > threshold_bps`.
    Writes each violation to `signals` if `write_signals` is True.
    """
    groups = await _partition_groups(pool)
    results = []
    for group_id, label in groups:
        contracts = await _group_contracts(pool, group_id)
        if len(contracts) < 2:
            continue
        result = _check_partition_arb(group_id, label, contracts)
        if result.violation_bps > threshold_bps:
            results.append(result)
            if write_signals:
                await _write_partition_signal(pool, result)
    return results


async def run_cross_venue_monitor(
    pool: asyncpg.Pool,
    *,
    threshold_bps: Decimal = Decimal("0"),
    write_signals: bool = True,
) -> list[CrossVenueArbResult]:
    """Check every 'cross_venue' market_group for price divergence."""
    pairs = await _cross_venue_pairs(pool)
    results = []
    for group_id, mid_a, venue_a, id_a, mid_b, venue_b, id_b in pairs:
        divergence = abs(mid_a - mid_b)
        divergence_bps = Decimal(str(round(float(divergence) * 10000, 2)))
        if divergence_bps <= threshold_bps:
            continue
        r = CrossVenueArbResult(
            group_id=group_id,
            market_id_a=id_a,
            venue_a=venue_a,
            p_mid_a=mid_a,
            market_id_b=id_b,
            venue_b=venue_b,
            p_mid_b=mid_b,
            divergence_bps=divergence_bps,
        )
        results.append(r)
        if write_signals:
            await _write_cross_venue_signal(pool, r)
    return results


# ---------------------------------------------------------------------------
# Aggregate stats helper
# ---------------------------------------------------------------------------


async def arb_aggregate_stats(
    pool: asyncpg.Pool,
    *,
    lookback_days: int = 30,
) -> ArbAggregateStats:
    """Compute aggregate arb statistics from historical signals.

    Queries `signals` for `arb_violation_bps` rows in the lookback window
    and returns violations/day and median severity.
    """
    cutoff = datetime.now(tz=UTC) - timedelta(days=lookback_days)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT value
            FROM   signals
            WHERE  signal_type = 'arb_violation_bps'
              AND  event_ts    >= $1
              AND  value       >  0
            ORDER  BY event_ts
            """,
            cutoff,
        )

    total = len(rows)
    violations_per_day = total / lookback_days if lookback_days > 0 else 0.0
    median_bps: float | None = None
    if rows:
        values = [float(r["value"]) for r in rows]
        median_bps = statistics.median(values)

    return ArbAggregateStats(
        lookback_days=lookback_days,
        total_violations=total,
        violations_per_day=round(violations_per_day, 3),
        median_severity_bps=round(median_bps, 2) if median_bps is not None else None,
    )


# ---------------------------------------------------------------------------
# LP-based partition check
# ---------------------------------------------------------------------------


def _check_partition_arb(
    group_id: UUID,
    label: str,
    contracts: list[dict[str, Any]],
) -> PartitionArbResult:
    """Run LP feasibility test on a market partition.

    For a set of N contracts with bid_i, ask_i, the feasibility LP asks:
      does there exist p ∈ [bid, ask]^N with sum(p) = 1?

    If infeasible:
      - sum(ask_i) < 1 → long arb: buy all at ask, collect 1, profit = 1 - sum(ask)
      - sum(bid_i) > 1 → short arb: sell all at bid, pay 1, profit = sum(bid) - 1
    """
    n = len(contracts)
    bids = np.array([float(c["best_bid"] or 0) for c in contracts])
    asks = np.array([float(c["best_ask"] or 1) for c in contracts])

    # Adjust for Kalshi fees: buying costs ask + fee, selling gives bid - fee.
    fee = float(KALSHI_FEE_PER_SIDE)
    eff_asks = asks + fee
    eff_bids = bids - fee

    ask_sum = Decimal(str(round(float(np.sum(asks)), 6)))
    bid_sum = Decimal(str(round(float(np.sum(bids)), 6)))
    eff_ask_sum = float(np.sum(eff_asks))
    eff_bid_sum = float(np.sum(eff_bids))

    # Fast check for partition markets.
    long_violation = max(0.0, 1.0 - eff_ask_sum)
    short_violation = max(0.0, eff_bid_sum - 1.0)
    raw_violation = max(long_violation, short_violation)

    if raw_violation == 0.0:
        # Confirm with LP for edge cases.
        c = np.zeros(n)
        A_eq = np.ones((1, n))
        b_eq = np.array([1.0])
        bounds = list(zip(bids.tolist(), asks.tolist(), strict=True))
        res = linprog(c, A_eq=A_eq, b_eq=b_eq, bounds=bounds, method="highs")
        if res.status == 0:  # feasible
            violation_bps = Decimal("0")
            direction = "none"
        else:
            # Infeasible despite raw check — recompute from effective sums.
            long_v = max(0.0, 1.0 - eff_ask_sum)
            short_v = max(0.0, eff_bid_sum - 1.0)
            raw = max(long_v, short_v)
            violation_bps = Decimal(str(round(raw * 10000, 2)))
            direction = "long" if long_v >= short_v else "short"
    else:
        violation_bps = Decimal(str(round(raw_violation * 10000, 2)))
        direction = "long" if long_violation >= short_violation else "short"

    # Depth feasibility: each contract must have non-zero size on the needed side.
    depth_feasible = False
    if violation_bps > 0:
        if direction == "long":
            depth_feasible = all(float(c["ask_size"] or 0) > 0 for c in contracts)
        else:
            depth_feasible = all(float(c["bid_size"] or 0) > 0 for c in contracts)

    return PartitionArbResult(
        group_id=group_id,
        group_label=label,
        n_contracts=n,
        min_ask_sum=ask_sum,
        max_bid_sum=bid_sum,
        violation_bps=violation_bps,
        direction=direction,
        depth_feasible=depth_feasible,
        market_ids=[UUID(str(c["market_id"])) for c in contracts],
    )


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def _partition_groups(pool: asyncpg.Pool) -> list[tuple[UUID, str]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, label FROM market_groups
            WHERE group_type = 'partition'
              AND label NOT LIKE 'KXFED%%'
            """
        )
    return [(UUID(str(r["id"])), r["label"]) for r in rows]


async def _group_contracts(pool: asyncpg.Pool, group_id: UUID) -> list[dict[str, Any]]:
    """Fetch latest best bid/ask and sizes for all markets in a group."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                m.id            AS market_id,
                t.bid           AS best_bid,
                t.ask           AS best_ask,
                t.bid_size      AS bid_size,
                t.ask_size      AS ask_size
            FROM markets m
            LEFT JOIN LATERAL (
                SELECT bid, ask, bid_size, ask_size
                FROM   ticks
                WHERE  market_id = m.id AND kind = 'quote'
                ORDER BY event_ts DESC, sequence_no DESC
                LIMIT 1
            ) t ON true
            WHERE m.market_group_id = $1
              AND m.resolution_status = 'open'
            """,
            group_id,
        )
    return [dict(r) for r in rows]


async def _cross_venue_pairs(
    pool: asyncpg.Pool,
) -> list[tuple[UUID, Decimal, str, UUID, Decimal, str, UUID]]:
    """
    Return (group_id, p_mid_a, venue_a, id_a, p_mid_b, venue_b, id_b)
    for each cross-venue pair where both sides have a recent p_mid signal.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            WITH cross_pairs AS (
                SELECT
                    mg.id           AS group_id,
                    m.id            AS market_id,
                    v.code          AS venue_code,
                    s.value         AS p_mid
                FROM market_groups mg
                JOIN markets m ON m.market_group_id = mg.id
                JOIN venues  v ON v.id = m.venue_id
                LEFT JOIN LATERAL (
                    SELECT value FROM signals
                    WHERE market_id = m.id AND signal_type = 'p_mid'
                    ORDER BY event_ts DESC LIMIT 1
                ) s ON true
                WHERE mg.group_type = 'cross_venue'
                  AND m.resolution_status = 'open'
                  AND s.value IS NOT NULL
            )
            SELECT
                a.group_id,
                a.p_mid   AS p_mid_a, a.venue_code AS venue_a, a.market_id AS id_a,
                b.p_mid   AS p_mid_b, b.venue_code AS venue_b, b.market_id AS id_b
            FROM cross_pairs a
            JOIN cross_pairs b
              ON a.group_id = b.group_id AND a.market_id < b.market_id
            """
        )
    return [
        (
            UUID(str(r["group_id"])),
            Decimal(str(r["p_mid_a"])),
            r["venue_a"],
            UUID(str(r["id_a"])),
            Decimal(str(r["p_mid_b"])),
            r["venue_b"],
            UUID(str(r["id_b"])),
        )
        for r in rows
    ]


async def _write_partition_signal(pool: asyncpg.Pool, r: PartitionArbResult) -> None:
    now = datetime.now(tz=UTC)
    meta = json.dumps(
        {
            "group_id": str(r.group_id),
            "group_label": r.group_label,
            "n_contracts": r.n_contracts,
            "direction": r.direction,
            "depth_feasible": r.depth_feasible,
            "ask_sum": str(r.min_ask_sum),
            "bid_sum": str(r.max_bid_sum),
            "market_ids": [str(m) for m in r.market_ids],
        }
    )
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO signals (event_ts, market_id, signal_type, value, metadata, ingest_ts)
            VALUES ($1, NULL, 'arb_violation_bps', $2, $3::jsonb, $4)
            ON CONFLICT DO NOTHING
            """,
            now,
            float(r.violation_bps),
            meta,
            now,
        )


async def _write_cross_venue_signal(pool: asyncpg.Pool, r: CrossVenueArbResult) -> None:
    now = datetime.now(tz=UTC)
    meta = json.dumps(
        {
            "group_id": str(r.group_id),
            "market_id_a": str(r.market_id_a),
            "venue_a": r.venue_a,
            "p_mid_a": str(r.p_mid_a),
            "market_id_b": str(r.market_id_b),
            "venue_b": r.venue_b,
            "p_mid_b": str(r.p_mid_b),
        }
    )
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO signals (event_ts, market_id, signal_type, value, metadata, ingest_ts)
            VALUES ($1, NULL, 'cross_venue_divergence_bps', $2, $3::jsonb, $4)
            ON CONFLICT DO NOTHING
            """,
            now,
            float(r.divergence_bps),
            meta,
            now,
        )
