"""Walk-forward evaluation harness for portfolio strategies.

Splits a return series into expanding-window train/test folds with no
look-ahead: at each step the optimizer sees only data up to the fold
boundary, then is evaluated on the subsequent out-of-sample period.

Example
-------
    folds = make_folds(returns, train_size=252, test_size=21, step=21)
    results = evaluate(folds, optimize_fn=optimize)
    print(summary(results))
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.typing import NDArray

# ---------------------------------------------------------------------------
# Fold construction
# ---------------------------------------------------------------------------


@dataclass
class Fold:
    """One walk-forward fold."""

    fold_id: int
    train: NDArray[np.float64]  # (train_size, N) in-sample returns
    test: NDArray[np.float64]   # (test_size, N) out-of-sample returns
    train_end: int               # index of last training row
    test_start: int
    test_end: int


def make_folds(
    returns: NDArray[np.float64],
    *,
    train_size: int,
    test_size: int,
    step: int | None = None,
    expanding: bool = True,
) -> list[Fold]:
    """Generate walk-forward folds from a (T, N) return matrix.

    Parameters
    ----------
    returns : (T, N) full return series.
    train_size : Minimum number of rows in the training window.
    test_size : Number of out-of-sample rows per fold.
    step : Step between folds (defaults to `test_size`).
    expanding : If True, training window grows at each step (expanding window).
                If False, training window stays fixed width (rolling window).
    """
    T = returns.shape[0]
    if step is None:
        step = test_size
    if train_size + test_size > T:
        raise ValueError(
            f"train_size ({train_size}) + test_size ({test_size}) > T ({T})."
        )

    folds = []
    fold_id = 0
    test_start = train_size
    while test_start + test_size <= T:
        test_end = test_start + test_size
        train_start = 0 if expanding else max(0, test_start - train_size)
        folds.append(
            Fold(
                fold_id=fold_id,
                train=returns[train_start:test_start],
                test=returns[test_start:test_end],
                train_end=test_start - 1,
                test_start=test_start,
                test_end=test_end,
            )
        )
        test_start += step
        fold_id += 1

    return folds


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


@dataclass
class FoldResult:
    """Out-of-sample result for one fold."""

    fold_id: int
    weights: NDArray[np.float64]
    oos_returns: NDArray[np.float64]   # per-period portfolio returns (test_size,)
    cumulative_return: float
    annualized_return: float
    annualized_vol: float
    sharpe: float | None
    max_drawdown: float
    train_size: int
    test_size: int
    optimizer_status: str


@dataclass
class WalkForwardSummary:
    """Aggregate statistics across all folds."""

    n_folds: int
    mean_sharpe: float | None
    mean_annualized_return: float
    mean_annualized_vol: float
    mean_max_drawdown: float
    fold_results: list[FoldResult] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"Walk-forward evaluation ({self.n_folds} folds)",
            f"Mean ann. return:  {self.mean_annualized_return:+.4f}",
            f"Mean ann. vol:     {self.mean_annualized_vol:.4f}",
        ]
        if self.mean_sharpe is not None:
            lines.append(f"Mean Sharpe:       {self.mean_sharpe:.4f}")
        lines.append(f"Mean max drawdown: {self.mean_max_drawdown:.4f}")
        lines.append("")
        lines.append(f"{'Fold':>5}  {'Sharpe':>8}  {'Ann.Ret':>9}  {'MaxDD':>8}")
        lines.append("─" * 38)
        for fr in self.fold_results:
            sharpe_str = f"{fr.sharpe:.3f}" if fr.sharpe is not None else "  n/a"
            lines.append(
                f"{fr.fold_id:>5}  {sharpe_str:>8}  "
                f"{fr.annualized_return:>+9.4f}  {fr.max_drawdown:>8.4f}"
            )
        return "\n".join(lines)


def _compute_fold_result(
    fold_id: int,
    weights: NDArray[np.float64],
    test: NDArray[np.float64],
    optimizer_status: str,
) -> FoldResult:
    oos = test @ weights  # (test_size,) portfolio returns
    T = len(oos)
    cum = float(np.prod(1 + oos) - 1)
    ann_ret = float((1 + cum) ** (252 / max(T, 1)) - 1)
    ann_vol = float(np.std(oos, ddof=1) * np.sqrt(252)) if T > 1 else 0.0
    sharpe = (ann_ret / ann_vol) if ann_vol > 1e-12 else None
    # Max drawdown via cumulative wealth.
    wealth = np.cumprod(1 + oos)
    running_max = np.maximum.accumulate(wealth)
    drawdowns = (wealth - running_max) / running_max
    max_dd = float(np.min(drawdowns))

    return FoldResult(
        fold_id=fold_id,
        weights=weights,
        oos_returns=oos,
        cumulative_return=cum,
        annualized_return=ann_ret,
        annualized_vol=ann_vol,
        sharpe=sharpe,
        max_drawdown=max_dd,
        train_size=len(test),
        test_size=T,
        optimizer_status=optimizer_status,
    )


def evaluate(
    folds: list[Fold],
    *,
    optimize_fn: Callable[..., Any],
    **optimizer_kwargs: Any,
) -> WalkForwardSummary:
    """Run `optimize_fn(train_returns, **optimizer_kwargs)` on each fold.

    `optimize_fn` must return an object with a `.weights` attribute
    (NDArray) and a `.status` attribute (str) — compatible with
    `research.portfolio.PortfolioResult`.
    """
    fold_results: list[FoldResult] = []

    for fold in folds:
        try:
            opt = optimize_fn(fold.train, **optimizer_kwargs)
            weights: NDArray[np.float64] = opt.weights
            status: str = opt.status
        except Exception as exc:
            N = fold.train.shape[1]
            weights = np.full(N, 1.0 / N)
            status = f"error: {exc}"

        fr = _compute_fold_result(fold.fold_id, weights, fold.test, status)
        fold_results.append(fr)

    n = len(fold_results)
    if n == 0:
        return WalkForwardSummary(
            n_folds=0,
            mean_sharpe=None,
            mean_annualized_return=0.0,
            mean_annualized_vol=0.0,
            mean_max_drawdown=0.0,
            fold_results=[],
        )

    sharpes = [fr.sharpe for fr in fold_results if fr.sharpe is not None]
    mean_sharpe = float(np.mean(sharpes)) if sharpes else None
    mean_ann_ret = float(np.mean([fr.annualized_return for fr in fold_results]))
    mean_ann_vol = float(np.mean([fr.annualized_vol for fr in fold_results]))
    mean_max_dd = float(np.mean([fr.max_drawdown for fr in fold_results]))

    return WalkForwardSummary(
        n_folds=n,
        mean_sharpe=mean_sharpe,
        mean_annualized_return=mean_ann_ret,
        mean_annualized_vol=mean_ann_vol,
        mean_max_drawdown=mean_max_dd,
        fold_results=fold_results,
    )
