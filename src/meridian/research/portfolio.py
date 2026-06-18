"""Portfolio optimizer: Markowitz mean-variance with Ledoit-Wolf shrinkage.

Uses `cvxpy` for the QP solver and `numpy` for covariance estimation.
All inputs and outputs use plain numpy arrays (no pandas dependency).

The optimizer solves:

    minimize    w.T @ Σ @ w
    subject to  w.T @ μ >= target_return  (optional)
                sum(w) == 1
                w >= 0  (long-only)

where `Σ` is the Ledoit-Wolf shrunk covariance matrix.

Walk-forward cross-validation is in `walkforward.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cvxpy as cp
import numpy as np
from numpy.typing import NDArray

# ---------------------------------------------------------------------------
# Covariance estimation
# ---------------------------------------------------------------------------


def ledoit_wolf_shrinkage(returns: NDArray[np.float64]) -> NDArray[np.float64]:
    """Ledoit-Wolf analytical shrinkage estimator for covariance.

    Returns the shrunk covariance matrix using the Ledoit-Wolf formula
    (Oracle Approximating Shrinkage, OAS variant from sklearn).

    `returns` shape: (T, N) — T observations, N assets.
    """
    from sklearn.covariance import OAS

    oas = OAS()
    oas.fit(returns)
    return oas.covariance_  # type: ignore[no-any-return]


def sample_covariance(returns: NDArray[np.float64]) -> NDArray[np.float64]:
    """Plain sample covariance matrix (no shrinkage)."""
    return np.cov(returns, rowvar=False)


# ---------------------------------------------------------------------------
# Optimizer
# ---------------------------------------------------------------------------


@dataclass
class PortfolioResult:
    """Output of the mean-variance optimizer."""

    weights: NDArray[np.float64]  # (N,) optimal weights
    expected_return: float  # w.T @ mu
    expected_variance: float  # w.T @ Sigma @ w
    expected_vol: float  # sqrt(variance)
    sharpe: float | None  # (E[r] - rf) / vol; None when vol == 0
    status: str  # cvxpy solve status
    n_assets: int

    @property
    def success(self) -> bool:
        return self.status in ("optimal", "optimal_inaccurate")

    def summary(self) -> str:
        lines = [
            f"Portfolio ({self.n_assets} assets)",
            f"Status:    {self.status}",
            f"E[return]: {self.expected_return:.4f}",
            f"Vol:       {self.expected_vol:.4f}",
        ]
        if self.sharpe is not None:
            lines.append(f"Sharpe:    {self.sharpe:.4f}")
        lines.append("")
        lines.append(f"{'Asset':>6}  {'Weight':>8}")
        lines.append("─" * 18)
        for i, w in enumerate(self.weights):
            if abs(w) > 1e-4:
                lines.append(f"{i:>6}  {w:>8.4f}")
        return "\n".join(lines)


def optimize(
    returns: NDArray[np.float64],
    *,
    target_return: float | None = None,
    risk_free_rate: float = 0.0,
    shrink: bool = True,
    long_only: bool = True,
    max_weight: float = 1.0,
) -> PortfolioResult:
    """Compute the minimum-variance (or target-return) portfolio.

    Parameters
    ----------
    returns : (T, N) array of period returns per asset.
    target_return : If given, adds a constraint `w @ mu >= target_return`.
    risk_free_rate : Used only for Sharpe ratio calculation (not a constraint).
    shrink : Use Ledoit-Wolf OAS shrinkage (default True).
    long_only : Enforce w >= 0 (default True).
    max_weight : Upper bound on individual weights (default 1.0 = unconstrained).
    """
    T, N = returns.shape
    if T < N:
        raise ValueError(
            f"Need at least as many observations as assets (got T={T}, N={N}). "
            "Consider a longer lookback window."
        )

    mu = np.mean(returns, axis=0)
    sigma = ledoit_wolf_shrinkage(returns) if shrink else sample_covariance(returns)

    w = cp.Variable(N)
    constraints: list[Any] = [cp.sum(w) == 1]  # type: ignore[attr-defined]
    if long_only:
        constraints.append(w >= 0)
    if max_weight < 1.0:
        constraints.append(w <= max_weight)
    if target_return is not None:
        constraints.append(mu @ w >= target_return)

    objective = cp.Minimize(cp.quad_form(w, sigma))  # type: ignore[attr-defined]
    prob = cp.Problem(objective, constraints)
    prob.solve(solver=cp.CLARABEL, verbose=False)  # type: ignore[no-untyped-call]

    status = prob.status or "unknown"
    if w.value is None:
        weights = np.full(N, 1.0 / N)  # fallback: equal-weight
    else:
        weights = np.array(w.value, dtype=np.float64)
        weights = np.clip(weights, 0.0, None)
        total = weights.sum()
        if total > 0:
            weights /= total

    exp_ret = float(mu @ weights)
    exp_var = float(weights @ sigma @ weights)
    exp_vol = float(np.sqrt(max(exp_var, 0.0)))
    sharpe = (exp_ret - risk_free_rate) / exp_vol if exp_vol > 1e-12 else None

    return PortfolioResult(
        weights=weights,
        expected_return=exp_ret,
        expected_variance=exp_var,
        expected_vol=exp_vol,
        sharpe=sharpe,
        status=status,
        n_assets=N,
    )


def efficient_frontier(
    returns: NDArray[np.float64],
    *,
    n_points: int = 20,
    shrink: bool = True,
    long_only: bool = True,
) -> list[PortfolioResult]:
    """Compute `n_points` portfolios along the efficient frontier.

    Sweeps target return from the minimum-variance portfolio's return
    to the maximum-return portfolio's return.
    """
    mu = np.mean(returns, axis=0)
    r_min = float(optimize(returns, shrink=shrink, long_only=long_only).expected_return)
    r_max = float(np.max(mu))
    if r_max <= r_min:
        return [optimize(returns, shrink=shrink, long_only=long_only)]

    results = []
    for target in np.linspace(r_min, r_max, n_points):
        try:
            res = optimize(returns, target_return=float(target), shrink=shrink, long_only=long_only)
        except Exception:
            continue
        results.append(res)
    return results
