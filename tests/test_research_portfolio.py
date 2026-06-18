"""Unit tests for research.portfolio — pure functions only (no DB, no network)."""

from __future__ import annotations

import numpy as np
import pytest

from meridian.research.portfolio import (
    efficient_frontier,
    ledoit_wolf_shrinkage,
    optimize,
    sample_covariance,
)


def _rng(seed: int = 42) -> np.random.Generator:
    return np.random.default_rng(seed)


def _returns(T: int = 100, N: int = 5, seed: int = 42) -> np.ndarray:
    rng = _rng(seed)
    return rng.normal(0.001, 0.02, size=(T, N))


# ---------------------------------------------------------------------------
# Covariance estimators
# ---------------------------------------------------------------------------


def test_sample_covariance_shape() -> None:
    r = _returns(50, 4)
    sigma = sample_covariance(r)
    assert sigma.shape == (4, 4)


def test_sample_covariance_symmetric_psd() -> None:
    r = _returns(100, 5)
    sigma = sample_covariance(r)
    assert np.allclose(sigma, sigma.T)
    eigenvalues = np.linalg.eigvalsh(sigma)
    assert np.all(eigenvalues >= -1e-10)


def test_ledoit_wolf_shape_and_psd() -> None:
    r = _returns(100, 5)
    sigma = ledoit_wolf_shrinkage(r)
    assert sigma.shape == (5, 5)
    assert np.allclose(sigma, sigma.T, atol=1e-10)
    eigenvalues = np.linalg.eigvalsh(sigma)
    assert np.all(eigenvalues >= -1e-10)


def test_ledoit_wolf_shrinks_toward_identity() -> None:
    # Shrinkage should produce a covariance with smaller off-diagonal entries
    # relative to sample covariance for small T.
    r = _returns(30, 10)  # T < N — high-dimensional regime
    sigma_sample = sample_covariance(r)
    sigma_lw = ledoit_wolf_shrinkage(r)
    # Off-diagonal sums: shrinkage should reduce them.
    off_sample = np.sum(np.abs(sigma_sample - np.diag(np.diag(sigma_sample))))
    off_lw = np.sum(np.abs(sigma_lw - np.diag(np.diag(sigma_lw))))
    assert off_lw <= off_sample + 1e-10


# ---------------------------------------------------------------------------
# optimize
# ---------------------------------------------------------------------------


def test_optimize_weights_sum_to_one() -> None:
    r = _returns()
    result = optimize(r)
    assert abs(sum(result.weights) - 1.0) < 1e-6


def test_optimize_long_only_no_negative_weights() -> None:
    r = _returns()
    result = optimize(r, long_only=True)
    assert np.all(result.weights >= -1e-6)


def test_optimize_allows_short_when_long_only_false() -> None:
    r = _returns(200, 3)
    result = optimize(r, long_only=False)
    # Not guaranteed to be short, but the constraint is relaxed.
    assert result.success


def test_optimize_variance_minimized() -> None:
    r = _returns()
    result = optimize(r)
    # Min-var portfolio variance must be <= equal-weight variance.
    N = r.shape[1]
    ew = np.full(N, 1.0 / N)
    sigma = sample_covariance(r)
    ew_var = float(ew @ sigma @ ew)
    assert result.expected_variance <= ew_var + 1e-6


def test_optimize_target_return_feasibility() -> None:
    r = _returns()
    mu = np.mean(r, axis=0)
    # Target the mean return — should be feasible.
    target = float(np.mean(mu))
    result = optimize(r, target_return=target)
    assert result.success
    assert result.expected_return >= target - 1e-4


def test_optimize_raises_when_t_less_than_n() -> None:
    r = _returns(T=3, N=10)
    with pytest.raises(ValueError, match="at least as many observations"):
        optimize(r)


def test_optimize_no_shrink() -> None:
    r = _returns()
    result = optimize(r, shrink=False)
    assert result.success
    assert abs(sum(result.weights) - 1.0) < 1e-5


def test_optimize_max_weight_constraint() -> None:
    r = _returns(200, 4)
    result = optimize(r, max_weight=0.4)
    assert result.success
    assert np.all(result.weights <= 0.4 + 1e-6)


# ---------------------------------------------------------------------------
# PortfolioResult
# ---------------------------------------------------------------------------


def test_portfolio_result_sharpe() -> None:
    r = _returns()
    result = optimize(r, risk_free_rate=0.0)
    if result.expected_vol > 1e-12:
        assert result.sharpe is not None
        assert abs(result.sharpe - result.expected_return / result.expected_vol) < 1e-6


def test_portfolio_result_summary_contains_status() -> None:
    r = _returns()
    result = optimize(r)
    s = result.summary()
    assert result.status in s
    assert "Portfolio" in s


# ---------------------------------------------------------------------------
# efficient_frontier
# ---------------------------------------------------------------------------


def test_efficient_frontier_returns_multiple_points() -> None:
    r = _returns(200, 4)
    results = efficient_frontier(r, n_points=5)
    assert len(results) >= 1


def test_efficient_frontier_monotone_return_with_vol() -> None:
    r = _returns(300, 4)
    results = efficient_frontier(r, n_points=8)
    # Expected return should be non-decreasing as we move along the frontier.
    returns = [res.expected_return for res in results if res.success]
    import itertools

    for a, b in itertools.pairwise(returns):
        assert b >= a - 1e-5
