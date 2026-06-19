"""Unit tests for analytics.calibration: Brier score, log loss, reliability diagram,
isotonic recalibration, and DB-backed calibration functions.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import numpy as np
import pytest

from meridian.analytics.calibration import (
    CalibrationResult,
    ReliabilityBin,
    _market_pmid_history,
    _resolved_markets,
    brier_score,
    isotonic_recalibrate,
    log_loss,
    reliability_diagram,
    run_calibration,
    write_calibration_signals,
)

# ---------------------------------------------------------------------------
# Brier score
# ---------------------------------------------------------------------------


def test_brier_perfect_predictions() -> None:
    p = np.array([1.0, 0.0, 1.0, 0.0])
    o = np.array([1.0, 0.0, 1.0, 0.0])
    assert brier_score(p, o) == pytest.approx(0.0)


def test_brier_worst_predictions() -> None:
    p = np.array([0.0, 1.0])
    o = np.array([1.0, 0.0])
    assert brier_score(p, o) == pytest.approx(1.0)


def test_brier_uniform_half() -> None:
    # All predictions 0.5 against 100% positive outcomes → BS = 0.25
    p = np.full(100, 0.5)
    o = np.ones(100)
    assert brier_score(p, o) == pytest.approx(0.25)


def test_brier_symmetry() -> None:
    rng = np.random.default_rng(42)
    p = rng.uniform(0, 1, 50)
    o = rng.integers(0, 2, 50).astype(float)
    # Brier score is symmetric in the sense that bs(p, o) == bs(1-p, 1-o).
    assert brier_score(p, o) == pytest.approx(brier_score(1.0 - p, 1.0 - o))


# ---------------------------------------------------------------------------
# Log loss
# ---------------------------------------------------------------------------


def test_log_loss_perfect_clipped() -> None:
    p = np.array([1.0, 0.0])
    o = np.array([1.0, 0.0])
    # With eps clipping, log(1-eps) ≈ 0
    assert log_loss(p, o) < 1e-5


def test_log_loss_uniform() -> None:
    # Predicting 0.5 always: log loss = log(2) ≈ 0.693
    p = np.full(1000, 0.5)
    o = np.ones(1000)
    assert log_loss(p, o) == pytest.approx(np.log(2), abs=0.01)


def test_log_loss_positive_finite() -> None:
    rng = np.random.default_rng(7)
    p = rng.uniform(0.1, 0.9, 100)
    o = rng.integers(0, 2, 100).astype(float)
    ll = log_loss(p, o)
    assert ll > 0
    assert np.isfinite(ll)


# ---------------------------------------------------------------------------
# Reliability diagram
# ---------------------------------------------------------------------------


def test_reliability_bins_count() -> None:
    rng = np.random.default_rng(0)
    p = rng.uniform(0, 1, 1000)
    o = (p > 0.5).astype(float)
    bins = reliability_diagram(p, o, n_bins=10)
    # Every bin should be non-empty given 1000 samples.
    assert len(bins) == 10
    assert all(isinstance(b, ReliabilityBin) for b in bins)


def test_reliability_bins_include_one() -> None:
    """A probability of exactly 1.0 must fall in the last bin."""
    p = np.array([0.95, 1.0])
    o = np.array([1.0, 1.0])
    bins = reliability_diagram(p, o, n_bins=10)
    last = bins[-1]
    assert last.count == 2
    assert last.upper == pytest.approx(1.0)


def test_reliability_mean_predicted_in_range() -> None:
    rng = np.random.default_rng(1)
    p = rng.uniform(0, 1, 500)
    o = rng.integers(0, 2, 500).astype(float)
    bins = reliability_diagram(p, o, n_bins=5)
    for b in bins:
        assert b.lower <= b.mean_predicted < b.upper or b.mean_predicted == 1.0
        assert 0.0 <= b.mean_realized <= 1.0
        assert b.count > 0


# ---------------------------------------------------------------------------
# Isotonic recalibration
# ---------------------------------------------------------------------------


def test_isotonic_recalibrate_shape() -> None:
    p = np.array([0.1, 0.3, 0.5, 0.7, 0.9])
    o = np.array([0.0, 0.0, 1.0, 1.0, 1.0])
    p_cal = isotonic_recalibrate(p, o)
    assert p_cal.shape == p.shape


def test_isotonic_recalibrate_monotone() -> None:
    rng = np.random.default_rng(99)
    p = rng.uniform(0, 1, 50)
    o = (p + rng.normal(0, 0.1, 50)).clip(0, 1).round()
    p_cal = isotonic_recalibrate(p, o)
    # Sorted by original p → isotonic output is non-decreasing.
    order = np.argsort(p)
    assert np.all(np.diff(p_cal[order]) >= -1e-9)


def test_isotonic_reduces_brier_on_miscalibrated_signal() -> None:
    """Isotonic recalibration should not increase Brier score."""
    rng = np.random.default_rng(13)
    # Generate over-confident predictions (closer to 0/1 than reality).
    true_prob = rng.uniform(0.3, 0.7, 200)
    outcomes = rng.binomial(1, true_prob).astype(float)
    predictions = np.clip(true_prob + rng.normal(0, 0.3, 200), 0, 1)

    bs_before = brier_score(predictions, outcomes)
    p_cal = isotonic_recalibrate(predictions, outcomes)
    bs_after = brier_score(p_cal, outcomes)

    assert bs_after <= bs_before + 1e-10, (
        "Isotonic recalibration should not increase Brier score on the training set"
    )


# ---------------------------------------------------------------------------
# Mock DB helpers for the DB-backed tests below
# ---------------------------------------------------------------------------

_MARKET_ID = UUID("12345678-1234-5678-1234-567812345678")


class _MockConn:
    """Minimal connection mock: returns fetch results in order, records executemany calls."""

    def __init__(self, fetch_results: list[list[dict[str, Any]]]) -> None:
        self._results: list[list[dict[str, Any]]] = list(fetch_results)
        self._index = 0
        self.executemany_calls: list[tuple[str, list[Any]]] = []

    async def fetch(self, query: str, *args: object) -> list[dict[str, Any]]:
        if self._index < len(self._results):
            result = self._results[self._index]
            self._index += 1
            return result
        return []

    async def executemany(self, query: str, records: Any) -> None:
        self.executemany_calls.append((query, list(records)))


class _MockPool:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[Any]:
        yield self._conn


# ---------------------------------------------------------------------------
# CalibrationResult.summary()
# ---------------------------------------------------------------------------


def test_summary_includes_all_fields() -> None:
    result = CalibrationResult(
        category="fed",
        lookback_days=30,
        n_markets=5,
        n_observations=100,
        brier_score=0.1234,
        log_loss=0.4567,
        reliability_bins=[
            ReliabilityBin(lower=0.0, upper=0.1, mean_predicted=0.05, mean_realized=0.04, count=10),
        ],
        brier_after_isotonic=0.1100,
    )
    s = result.summary()
    assert "fed" in s
    assert "30" in s
    assert "0.1234" in s
    assert "0.1100" in s
    assert "Brier (isotonic)" in s
    assert "[0.0, 0.1)" in s


def test_summary_no_category_no_isotonic() -> None:
    result = CalibrationResult(
        category=None,
        lookback_days=None,
        n_markets=2,
        n_observations=20,
        brier_score=0.2,
        log_loss=0.5,
        reliability_bins=[],
        brier_after_isotonic=None,
    )
    s = result.summary()
    assert "all" in s
    assert "Brier (isotonic)" not in s


# ---------------------------------------------------------------------------
# _resolved_markets()
# ---------------------------------------------------------------------------


async def test_resolved_markets_returns_parsed_rows() -> None:
    conn = _MockConn([[{"id": str(_MARKET_ID), "settled_value": 1.0}]])
    result = await _resolved_markets(_MockPool(conn), category=None)
    assert result == [(_MARKET_ID, 1.0)]


async def test_resolved_markets_with_category_appends_filter() -> None:
    received: list[str] = []

    class _TrackingConn:
        async def fetch(self, query: str, *args: object) -> list[dict[str, Any]]:
            received.append(query)
            return []

    await _resolved_markets(_MockPool(_TrackingConn()), category="fed")
    assert "AND category = $1" in received[0]


# ---------------------------------------------------------------------------
# _market_pmid_history()
# ---------------------------------------------------------------------------


async def test_market_pmid_history_returns_floats() -> None:
    conn = _MockConn([[{"value": 0.6}, {"value": 0.7}]])
    result = await _market_pmid_history(_MockPool(conn), _MARKET_ID, since=None)
    assert result == pytest.approx([0.6, 0.7])


async def test_market_pmid_history_with_since_appends_filter() -> None:
    received: list[str] = []

    class _TrackingConn:
        async def fetch(self, query: str, *args: object) -> list[dict[str, Any]]:
            received.append(query)
            return [{"value": 0.55}]

    cutoff = datetime(2026, 1, 1, tzinfo=UTC)
    result = await _market_pmid_history(_MockPool(_TrackingConn()), _MARKET_ID, since=cutoff)
    assert "AND event_ts >= $2" in received[0]
    assert result == pytest.approx([0.55])


async def test_market_pmid_history_filters_none_and_non_finite() -> None:
    conn = _MockConn([[{"value": None}, {"value": 0.5}, {"value": float("inf")}]])
    result = await _market_pmid_history(_MockPool(conn), _MARKET_ID, since=None)
    assert result == pytest.approx([0.5])


# ---------------------------------------------------------------------------
# run_calibration()
# ---------------------------------------------------------------------------


async def test_run_calibration_no_markets_returns_none() -> None:
    conn = _MockConn([[]])
    assert await run_calibration(_MockPool(conn)) is None


async def test_run_calibration_no_probs_returns_none() -> None:
    conn = _MockConn([
        [{"id": str(_MARKET_ID), "settled_value": 1.0}],
        [],  # p_mid history → empty
    ])
    assert await run_calibration(_MockPool(conn)) is None


async def test_run_calibration_returns_result() -> None:
    conn = _MockConn([
        [{"id": str(_MARKET_ID), "settled_value": 1.0}],
        [{"value": 0.7}, {"value": 0.8}],
    ])
    result = await run_calibration(_MockPool(conn), n_bins=5)
    assert result is not None
    assert result.n_markets == 1
    assert result.n_observations == 2
    assert result.brier_after_isotonic is not None


async def test_run_calibration_single_observation_skips_isotonic() -> None:
    conn = _MockConn([
        [{"id": str(_MARKET_ID), "settled_value": 1.0}],
        [{"value": 0.7}],
    ])
    result = await run_calibration(_MockPool(conn))
    assert result is not None
    assert result.n_observations == 1
    assert result.brier_after_isotonic is None


# ---------------------------------------------------------------------------
# write_calibration_signals()
# ---------------------------------------------------------------------------


async def test_write_calibration_signals_inserts_correct_records() -> None:
    result = CalibrationResult(
        category="fed",
        lookback_days=30,
        n_markets=2,
        n_observations=10,
        brier_score=0.15,
        log_loss=0.4,
        reliability_bins=[
            ReliabilityBin(lower=0.0, upper=0.5, mean_predicted=0.25, mean_realized=0.3, count=5),
        ],
        brier_after_isotonic=0.12,
    )
    conn = _MockConn([])
    await write_calibration_signals(_MockPool(conn), result)
    assert len(conn.executemany_calls) == 1
    query, records = conn.executemany_calls[0]
    assert "INSERT INTO signals" in query
    assert len(records) == 3  # brier + log_loss + 1 bin


async def test_write_calibration_signals_bin_gap_value() -> None:
    result = CalibrationResult(
        category=None,
        lookback_days=None,
        n_markets=1,
        n_observations=5,
        brier_score=0.2,
        log_loss=0.5,
        reliability_bins=[
            ReliabilityBin(lower=0.0, upper=0.5, mean_predicted=0.3, mean_realized=0.5, count=5),
        ],
        brier_after_isotonic=None,
    )
    conn = _MockConn([])
    await write_calibration_signals(_MockPool(conn), result)
    _, records = conn.executemany_calls[0]
    # reliability bin record: (event_ts, None, "calibration_reliability", gap, bin_meta, now)
    bin_gap = records[2][3]
    assert bin_gap == pytest.approx(0.5 - 0.3)
