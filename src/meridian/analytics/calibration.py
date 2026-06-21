"""Calibration engine: Brier score, log loss, reliability diagrams, isotonic recalibration.

Works on resolved markets (settled_value IS NOT NULL) by joining historical
p_mid signals to the final settlement outcome.

Usage (see also `cli/analytics.py`):

    result = await run_calibration(pool, category="fed", lookback=timedelta(days=90))
    print(result.summary())
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg
import numpy as np
import numpy.typing as npt
from sklearn.isotonic import IsotonicRegression

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class ReliabilityBin:
    """One bucket of the reliability diagram."""

    lower: float
    upper: float
    mean_predicted: float
    mean_realized: float
    count: int


@dataclass
class CalibrationResult:
    """Aggregate calibration metrics over a set of resolved markets."""

    category: str | None
    lookback_days: int | None
    n_markets: int
    n_observations: int
    brier_score: float
    log_loss: float
    reliability_bins: list[ReliabilityBin]
    # Isotonic-recalibrated Brier score (how much isotonic recalibration helps)
    brier_after_isotonic: float | None
    # Expected calibration error (ECE) over 10 equal-width bins
    ece: float | None = None

    def summary(self) -> str:
        lines = [
            f"Category:          {self.category or 'all'}",
            f"Lookback:          {self.lookback_days or 'all'} days",
            f"Markets:           {self.n_markets}",
            f"Observations:      {self.n_observations}",
            f"Brier score:       {self.brier_score:.4f}",
            f"Log loss:          {self.log_loss:.4f}",
        ]
        if self.ece is not None:
            lines.append(f"ECE (10-bin):      {self.ece:.4f}")
        if self.brier_after_isotonic is not None:
            lines.append(f"Brier (isotonic):  {self.brier_after_isotonic:.4f}")
        lines.append("\nReliability bins:")
        for b in self.reliability_bins:
            lines.append(
                f"  [{b.lower:.1f}, {b.upper:.1f})  "
                f"predicted={b.mean_predicted:.3f}  "
                f"realized={b.mean_realized:.3f}  "
                f"n={b.count}"
            )
        return "\n".join(lines)


@dataclass
class MarketCalibrationPoint:
    market_id: UUID
    probabilities: list[float] = field(default_factory=list)
    outcome: float = 0.0


# ---------------------------------------------------------------------------
# Pure calibration functions (no DB)
# ---------------------------------------------------------------------------


def brier_score(probabilities: npt.NDArray[np.float64], outcomes: npt.NDArray[np.float64]) -> float:
    """Mean squared error between predicted probabilities and binary outcomes."""
    return float(np.mean((probabilities - outcomes) ** 2))


def log_loss(
    probabilities: npt.NDArray[np.float64],
    outcomes: npt.NDArray[np.float64],
    *,
    eps: float = 1e-7,
) -> float:
    """Binary cross-entropy between probabilities and outcomes.

    Clips probabilities to [eps, 1-eps] to avoid log(0).
    """
    p = np.clip(probabilities, eps, 1.0 - eps)
    return float(-np.mean(outcomes * np.log(p) + (1.0 - outcomes) * np.log(1.0 - p)))


def reliability_diagram(
    probabilities: npt.NDArray[np.float64],
    outcomes: npt.NDArray[np.float64],
    *,
    n_bins: int = 10,
) -> list[ReliabilityBin]:
    """Bin [0,1] into `n_bins` equal-width buckets.

    Returns one `ReliabilityBin` per non-empty bucket with the mean
    predicted probability and mean realized outcome for that bucket.
    """
    bins: list[ReliabilityBin] = []
    width = 1.0 / n_bins
    for i in range(n_bins):
        lo = i * width
        hi = lo + width
        # Right-open except the last bucket, which is right-closed.
        mask = (probabilities >= lo) & (probabilities < hi)
        if i == n_bins - 1:
            mask |= probabilities == 1.0
        subset_p = probabilities[mask]
        subset_o = outcomes[mask]
        if len(subset_p) == 0:
            continue
        bins.append(
            ReliabilityBin(
                lower=lo,
                upper=hi,
                mean_predicted=float(np.mean(subset_p)),
                mean_realized=float(np.mean(subset_o)),
                count=len(subset_p),
            )
        )
    return bins


def expected_calibration_error(
    probabilities: npt.NDArray[np.float64],
    outcomes: npt.NDArray[np.float64],
    *,
    n_bins: int = 10,
) -> float:
    """Expected calibration error (ECE) using n_bins equal-width bins.

    ECE = Σ_b (|mean_predicted_b − mean_realized_b|) × (n_b / N)

    A perfectly calibrated forecaster has ECE = 0.
    """
    n = len(probabilities)
    if n == 0:
        return 0.0
    bins = reliability_diagram(probabilities, outcomes, n_bins=n_bins)
    return float(sum(abs(b.mean_predicted - b.mean_realized) * b.count / n for b in bins))


def isotonic_recalibrate(
    probabilities: npt.NDArray[np.float64],
    outcomes: npt.NDArray[np.float64],
) -> npt.NDArray[np.float64]:
    """Fit isotonic regression and return recalibrated probabilities.

    Isotonic regression is a non-parametric monotone transformation that
    minimises squared error subject to monotonicity — a common post-hoc
    calibration method.
    """
    iso = IsotonicRegression(out_of_bounds="clip")
    return iso.fit_transform(probabilities, outcomes)  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# DB-backed calibration runner
# ---------------------------------------------------------------------------


async def run_calibration(
    pool: asyncpg.Pool,
    *,
    category: str | None = None,
    lookback: timedelta | None = None,
    n_bins: int = 10,
) -> CalibrationResult | None:
    """Run calibration over resolved markets, returning aggregate metrics.

    Returns None if there are no resolved markets with p_mid signals in the
    requested window.
    """
    cutoff = datetime.now(tz=UTC) - lookback if lookback else None
    markets = await _resolved_markets(pool, category=category)
    if not markets:
        return None

    all_probs: list[float] = []
    all_outcomes: list[float] = []

    for market_id, outcome in markets:
        probs = await _market_pmid_history(pool, market_id, since=cutoff)
        all_probs.extend(probs)
        all_outcomes.extend([outcome] * len(probs))

    if not all_probs:
        return None

    p = np.array(all_probs, dtype=np.float64)
    o = np.array(all_outcomes, dtype=np.float64)

    bs = brier_score(p, o)
    ll = log_loss(p, o)
    rel = reliability_diagram(p, o, n_bins=n_bins)
    ece = expected_calibration_error(p, o, n_bins=n_bins)

    brier_iso: float | None = None
    if len(p) >= 2:
        p_iso = isotonic_recalibrate(p, o)
        brier_iso = brier_score(p_iso, o)

    return CalibrationResult(
        category=category,
        lookback_days=lookback.days if lookback else None,
        n_markets=len(markets),
        n_observations=len(all_probs),
        brier_score=bs,
        log_loss=ll,
        reliability_bins=rel,
        brier_after_isotonic=brier_iso,
        ece=ece,
    )


async def write_calibration_signals(
    pool: asyncpg.Pool,
    result: CalibrationResult,
) -> None:
    """Persist calibration metrics to the signals table.

    Writes three rows: Brier score, log loss, and one row per reliability bin
    (as JSONB metadata).
    """
    now = datetime.now(tz=UTC)
    category_meta = json.dumps({"category": result.category, "lookback_days": result.lookback_days})
    records: list[tuple[Any, ...]] = [
        (now, None, "calibration_brier", result.brier_score, category_meta, now),
        (now, None, "calibration_log_loss", result.log_loss, category_meta, now),
    ]
    for b in result.reliability_bins:
        bin_meta = json.dumps(
            {
                "category": result.category,
                "bin_lower": b.lower,
                "bin_upper": b.upper,
                "count": b.count,
            }
        )
        gap = b.mean_realized - b.mean_predicted
        records.append((now, None, "calibration_reliability", gap, bin_meta, now))

    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO signals (event_ts, market_id, signal_type, value, metadata, ingest_ts)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6)
            ON CONFLICT DO NOTHING
            """,
            records,
        )


# ---------------------------------------------------------------------------
# Internal DB helpers
# ---------------------------------------------------------------------------


async def _resolved_markets(
    pool: asyncpg.Pool,
    *,
    category: str | None,
) -> list[tuple[UUID, float]]:
    """Return (market_id, settled_value) for all resolved markets, optionally filtered."""
    query = """
        SELECT id, settled_value
        FROM   markets
        WHERE  resolution_status = 'settled'
          AND  settled_value IS NOT NULL
    """
    params: list[Any] = []
    if category is not None:
        query += " AND category = $1"
        params.append(category)
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *params)
    return [(UUID(str(r["id"])), float(r["settled_value"])) for r in rows]


async def _market_pmid_history(
    pool: asyncpg.Pool,
    market_id: UUID,
    *,
    since: datetime | None,
) -> list[float]:
    """Return all p_mid signal values for a market, optionally since a cutoff."""
    query = """
        SELECT value
        FROM   signals
        WHERE  market_id    = $1
          AND  signal_type  = 'p_mid'
    """
    params: list[Any] = [market_id]
    if since is not None:
        query += " AND event_ts >= $2"
        params.append(since)
    query += " ORDER BY event_ts ASC"
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *params)
    return [
        float(r["value"])
        for r in rows
        if r["value"] is not None and math.isfinite(float(r["value"]))
    ]
