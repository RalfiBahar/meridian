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
from collections.abc import Sequence
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
    # Isotonic Brier. For run_calibration this is in-sample. For
    # run_temporal_calibration it is the held-out score. Read isotonic_method.
    brier_after_isotonic: float | None
    # Expected calibration error (ECE) over 10 equal-width bins
    ece: float | None = None
    evaluation_method: str = "in_sample_all_ticks"
    observation_unit: str = "p_mid_tick"
    isotonic_method: str = "in_sample_fit_on_scored_ticks"
    brier_isotonic_in_sample: float | None = None
    n_train_markets: int | None = None
    n_test_markets: int | None = None
    evaluation_start: datetime | None = None

    def summary(self) -> str:
        lines = [
            f"Evaluation:        {self.evaluation_method}",
            f"Observation unit:  {self.observation_unit}",
            f"Category:          {self.category or 'all'}",
            f"Lookback:          {self.lookback_days or 'all'} days",
            f"Markets:           {self.n_markets}",
            f"Observations:      {self.n_observations}",
            f"Brier score:       {self.brier_score:.4f}",
            f"Log loss:          {self.log_loss:.4f}",
        ]
        if self.evaluation_start is not None:
            lines.append(f"Eval start:        {self.evaluation_start.isoformat()}")
        if self.n_train_markets is not None:
            lines.append(f"Train markets:     {self.n_train_markets}")
        if self.n_test_markets is not None:
            lines.append(f"Test markets:      {self.n_test_markets}")
        if self.ece is not None:
            lines.append(f"ECE (10-bin):      {self.ece:.4f}")
        if self.brier_after_isotonic is not None:
            lines.append(
                f"Brier (isotonic):  {self.brier_after_isotonic:.4f} [{self.isotonic_method}]"
            )
        if self.brier_isotonic_in_sample is not None:
            lines.append(f"Brier (isotonic, train): {self.brier_isotonic_in_sample:.4f}")
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

    ECE = sum_b (|mean_predicted_b - mean_realized_b|) * (n_b / N)

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
    """Fit isotonic regression on these rows and return the in-sample fitted values.

    ``fit_transform`` scores the same observations it trains on. That number
    is an in-sample fit statistic. Held-out scoring is
    ``evaluate_temporal_holdout``.
    """
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    return iso.fit_transform(probabilities, outcomes)  # type: ignore[no-any-return]


def fit_isotonic(
    probabilities: npt.NDArray[np.float64],
    outcomes: npt.NDArray[np.float64],
) -> IsotonicRegression:
    """Fit a monotone map. The model is applied with ``apply_isotonic``."""
    if len(probabilities) < 2:
        raise ValueError("isotonic regression needs at least two training rows")
    if len(probabilities) != len(outcomes):
        raise ValueError("probabilities and outcomes must have the same length")
    model = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    model.fit(probabilities, outcomes)
    return model


def apply_isotonic(
    model: IsotonicRegression,
    probabilities: npt.NDArray[np.float64],
) -> npt.NDArray[np.float64]:
    """Apply a fitted isotonic map and clip the result to [0, 1]."""
    predicted = np.asarray(model.predict(probabilities), dtype=np.float64)
    return np.clip(predicted, 0.0, 1.0)


@dataclass(frozen=True)
class MarketForecast:
    """One pre-settlement forecast for one market.

    ``forecast_ts`` is the timestamp of the quote. It is not after
    ``settlement_ts``. The outcome is the market's settled 0/1 value.
    """

    market_id: UUID
    settlement_ts: datetime
    forecast_ts: datetime
    probability: float
    outcome: float


@dataclass(frozen=True)
class TemporalCalibration:
    """Held-out scores for one settlement-time split.

    Training markets are those with ``settlement_ts < evaluation_start``.
    Scored markets settled at or after that instant. Isotonic regression is
    fit only on the training markets. ``oos_brier`` is the raw forecast on
    the scored markets, before any recalibration.
    """

    evaluation_start: datetime
    n_train: int
    n_test: int
    oos_brier: float
    oos_log_loss: float
    oos_ece: float
    oos_bins: list[ReliabilityBin]
    oos_isotonic_brier: float | None
    in_sample_isotonic_brier: float | None


def point_in_time_forecast(
    quotes: Sequence[tuple[datetime, float]],
    as_of: datetime,
) -> tuple[datetime, float] | None:
    """Last finite quote at or before ``as_of``.

    Later quotes are ignored. Naive timestamps are ignored so they cannot
    be compared with an aware cutoff.
    """
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    eligible = [
        (ts, probability)
        for ts, probability in quotes
        if ts.tzinfo is not None and ts <= as_of and math.isfinite(probability)
    ]
    if not eligible:
        return None
    eligible.sort(key=lambda item: item[0])
    return eligible[-1]


def select_market_forecast(
    market_id: UUID,
    settlement_ts: datetime,
    outcome: float,
    quotes: Sequence[tuple[datetime, float]],
) -> MarketForecast | None:
    """One forecast per market: the last p_mid at or before settlement.

    A quote after settlement is not eligible. Markets with no earlier quote,
    a non-binary outcome, or a probability outside [0, 1] are dropped.
    """
    if settlement_ts.tzinfo is None:
        raise ValueError("settlement_ts must be timezone-aware")
    if outcome not in (0.0, 1.0):
        return None
    chosen = point_in_time_forecast(quotes, settlement_ts)
    if chosen is None:
        return None
    forecast_ts, probability = chosen
    if not 0.0 <= probability <= 1.0:
        return None
    return MarketForecast(
        market_id=market_id,
        settlement_ts=settlement_ts,
        forecast_ts=forecast_ts,
        probability=probability,
        outcome=outcome,
    )


def evaluate_temporal_holdout(
    forecasts: Sequence[MarketForecast],
    *,
    evaluation_start: datetime,
    n_bins: int = 10,
) -> TemporalCalibration | None:
    """Score markets settled on or after ``evaluation_start``.

    Returns None when that held-out set is empty. Duplicate market ids are
    rejected: the observation is one forecast per market, not one per tick.
    """
    if evaluation_start.tzinfo is None:
        raise ValueError("evaluation_start must be timezone-aware")
    ids = [forecast.market_id for forecast in forecasts]
    if len(ids) != len(set(ids)):
        raise ValueError("temporal calibration requires one forecast per market_id")

    ordered = sorted(
        forecasts,
        key=lambda forecast: (forecast.settlement_ts, str(forecast.market_id)),
    )
    train = [forecast for forecast in ordered if forecast.settlement_ts < evaluation_start]
    test = [forecast for forecast in ordered if forecast.settlement_ts >= evaluation_start]
    if not test:
        return None

    test_p = np.array([forecast.probability for forecast in test], dtype=np.float64)
    test_o = np.array([forecast.outcome for forecast in test], dtype=np.float64)
    oos_iso: float | None = None
    in_sample_iso: float | None = None
    if len(train) >= 2:
        train_p = np.array([forecast.probability for forecast in train], dtype=np.float64)
        train_o = np.array([forecast.outcome for forecast in train], dtype=np.float64)
        model = fit_isotonic(train_p, train_o)
        oos_iso = brier_score(apply_isotonic(model, test_p), test_o)
        in_sample_iso = brier_score(apply_isotonic(model, train_p), train_o)

    return TemporalCalibration(
        evaluation_start=evaluation_start,
        n_train=len(train),
        n_test=len(test),
        oos_brier=brier_score(test_p, test_o),
        oos_log_loss=log_loss(test_p, test_o),
        oos_ece=expected_calibration_error(test_p, test_o, n_bins=n_bins),
        oos_bins=reliability_diagram(test_p, test_o, n_bins=n_bins),
        oos_isotonic_brier=oos_iso,
        in_sample_isotonic_brier=in_sample_iso,
    )


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
        evaluation_method="in_sample_all_ticks",
        observation_unit="p_mid_tick",
        isotonic_method="in_sample_fit_on_scored_ticks",
    )


async def run_temporal_calibration(
    pool: asyncpg.Pool,
    *,
    evaluation_start: datetime,
    category: str | None = None,
    n_bins: int = 10,
) -> CalibrationResult | None:
    """Score one pre-settlement forecast per market on a later settlement cohort.

    Markets with no ``settled_at``, or with no ``p_mid`` at or before that
    time, are omitted. The Brier score, ECE, and reliability bins on the
    returned result are out of sample. ``brier_after_isotonic`` is the
    isotonic map fit on earlier settlements and applied to the held-out
    markets. ``brier_isotonic_in_sample`` is the same map scored on the
    training markets and is not an out-of-sample number.
    """
    if evaluation_start.tzinfo is None:
        raise ValueError("evaluation_start must be timezone-aware")
    markets = await _resolved_markets_with_time(pool, category=category)
    forecasts: list[MarketForecast] = []
    for market_id, outcome, settled_at in markets:
        quotes = await _market_pmid_series(pool, market_id)
        selected = select_market_forecast(market_id, settled_at, outcome, quotes)
        if selected is not None:
            forecasts.append(selected)

    held_out = evaluate_temporal_holdout(
        forecasts,
        evaluation_start=evaluation_start,
        n_bins=n_bins,
    )
    if held_out is None:
        return None

    return CalibrationResult(
        category=category,
        lookback_days=None,
        n_markets=held_out.n_test,
        n_observations=held_out.n_test,
        brier_score=held_out.oos_brier,
        log_loss=held_out.oos_log_loss,
        reliability_bins=held_out.oos_bins,
        brier_after_isotonic=held_out.oos_isotonic_brier,
        ece=held_out.oos_ece,
        evaluation_method="out_of_sample_markets_settled_on_or_after_cutoff",
        observation_unit="one_p_mid_per_market_at_or_before_settlement",
        isotonic_method="fit_on_earlier_settlements_scored_on_later_markets",
        brier_isotonic_in_sample=held_out.in_sample_isotonic_brier,
        n_train_markets=held_out.n_train,
        n_test_markets=held_out.n_test,
        evaluation_start=evaluation_start,
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
    category_meta = json.dumps(
        {
            "category": result.category,
            "lookback_days": result.lookback_days,
            "evaluation_method": result.evaluation_method,
            "observation_unit": result.observation_unit,
        }
    )
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


async def _resolved_markets_with_time(
    pool: asyncpg.Pool,
    *,
    category: str | None,
) -> list[tuple[UUID, float, datetime]]:
    """Resolved markets that have both an outcome and a settlement timestamp."""
    query = """
        SELECT id, settled_value, settled_at
        FROM   markets
        WHERE  resolution_status = 'settled'
          AND  settled_value IS NOT NULL
          AND  settled_at IS NOT NULL
    """
    params: list[Any] = []
    if category is not None:
        query += " AND category = $1"
        params.append(category)
    query += " ORDER BY settled_at ASC"
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *params)
    return [(UUID(str(row["id"])), float(row["settled_value"]), row["settled_at"]) for row in rows]


async def _market_pmid_series(
    pool: asyncpg.Pool,
    market_id: UUID,
) -> list[tuple[datetime, float]]:
    """Every finite p_mid for one market, oldest first."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT event_ts, value
            FROM   signals
            WHERE  market_id = $1
              AND  signal_type = 'p_mid'
            ORDER BY event_ts ASC
            """,
            market_id,
        )
    series: list[tuple[datetime, float]] = []
    for row in rows:
        if row["value"] is None or row["event_ts"] is None:
            continue
        value = float(row["value"])
        if math.isfinite(value):
            series.append((row["event_ts"], value))
    return series
