"""Unit tests for analytics.calibration: Brier score, log loss, reliability diagram,
isotonic recalibration — all pure functions, no DB.
"""

from __future__ import annotations

import numpy as np
import pytest

from meridian.analytics.calibration import (
    ReliabilityBin,
    brier_score,
    isotonic_recalibrate,
    log_loss,
    reliability_diagram,
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
