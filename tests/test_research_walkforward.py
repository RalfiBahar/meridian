"""Unit tests for research.walkforward — pure functions only."""

from __future__ import annotations

import numpy as np
import pytest

from meridian.research.portfolio import optimize
from meridian.research.walkforward import (
    FoldResult,
    WalkForwardSummary,
    _compute_fold_result,
    evaluate,
    make_folds,
)


def _returns(T: int = 100, N: int = 3, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.normal(0.001, 0.02, size=(T, N))


# ---------------------------------------------------------------------------
# make_folds
# ---------------------------------------------------------------------------


def test_make_folds_count() -> None:
    r = _returns(100, 3)
    folds = make_folds(r, train_size=40, test_size=20, step=20)
    # (100 - 40) // 20 = 3 folds
    assert len(folds) == 3


def test_make_folds_no_overlap() -> None:
    r = _returns(100, 3)
    folds = make_folds(r, train_size=40, test_size=20)
    for i in range(len(folds) - 1):
        assert folds[i].test_end <= folds[i + 1].test_start


def test_make_folds_expanding_grows_train() -> None:
    r = _returns(100, 3)
    folds = make_folds(r, train_size=30, test_size=20, expanding=True)
    sizes = [len(f.train) for f in folds]
    assert sizes == sorted(sizes)  # strictly increasing


def test_make_folds_rolling_constant_train() -> None:
    r = _returns(100, 3)
    folds = make_folds(r, train_size=30, test_size=20, expanding=False)
    sizes = [len(f.train) for f in folds]
    assert all(s == 30 for s in sizes)


def test_make_folds_raises_when_too_small() -> None:
    r = _returns(10, 3)
    with pytest.raises(ValueError, match="train_size"):
        make_folds(r, train_size=8, test_size=5)


def test_make_folds_fold_id_sequential() -> None:
    r = _returns(100, 3)
    folds = make_folds(r, train_size=40, test_size=20)
    for i, fold in enumerate(folds):
        assert fold.fold_id == i


# ---------------------------------------------------------------------------
# _compute_fold_result
# ---------------------------------------------------------------------------


def test_compute_fold_result_basic() -> None:
    N = 3
    weights = np.array([0.5, 0.3, 0.2])
    test = np.ones((10, N)) * 0.001  # flat positive returns
    fr = _compute_fold_result(0, weights, test, "optimal")
    assert fr.cumulative_return > 0
    assert fr.annualized_vol >= 0


def test_compute_fold_result_max_drawdown_negative() -> None:
    weights = np.array([1.0, 0.0, 0.0])
    # Single asset: -5% then -5% — significant drawdown.
    test = np.zeros((4, 3))
    test[:, 0] = [-0.05, -0.05, 0.01, 0.01]
    fr = _compute_fold_result(0, weights, test, "optimal")
    assert fr.max_drawdown < 0


def test_compute_fold_result_sharpe_none_on_zero_vol() -> None:
    weights = np.array([1.0, 0.0, 0.0])
    test = np.zeros((5, 3))  # zero returns → zero vol
    fr = _compute_fold_result(0, weights, test, "optimal")
    assert fr.sharpe is None


# ---------------------------------------------------------------------------
# evaluate
# ---------------------------------------------------------------------------


def test_evaluate_runs_and_returns_summary() -> None:
    r = _returns(120, 4)
    folds = make_folds(r, train_size=60, test_size=20)
    summary = evaluate(folds, optimize_fn=optimize)
    assert isinstance(summary, WalkForwardSummary)
    assert summary.n_folds == len(folds)
    assert len(summary.fold_results) == len(folds)


def test_evaluate_empty_folds_returns_zero_summary() -> None:
    summary = evaluate([], optimize_fn=optimize)
    assert summary.n_folds == 0
    assert summary.mean_sharpe is None


def test_evaluate_uses_fallback_on_optimizer_error() -> None:
    def bad_optimizer(r: np.ndarray, **kwargs: object) -> object:
        raise RuntimeError("optimizer failed")

    r = _returns(100, 3)
    folds = make_folds(r, train_size=40, test_size=20)
    # Should not raise — falls back to equal-weight.
    summary = evaluate(folds, optimize_fn=bad_optimizer)
    assert summary.n_folds == len(folds)
    for fr in summary.fold_results:
        assert "error" in fr.optimizer_status


# ---------------------------------------------------------------------------
# WalkForwardSummary.summary
# ---------------------------------------------------------------------------


def test_walk_forward_summary_format() -> None:
    r = _returns(100, 3)
    folds = make_folds(r, train_size=40, test_size=20)
    summary = evaluate(folds, optimize_fn=optimize)
    text = summary.summary()
    assert "Walk-forward" in text
    assert "Fold" in text


# ---------------------------------------------------------------------------
# FoldResult dataclass
# ---------------------------------------------------------------------------


def test_fold_result_is_dataclass() -> None:
    weights = np.array([0.5, 0.5])
    oos = np.array([0.01, -0.005, 0.02])
    fr = _compute_fold_result(0, weights, np.column_stack([oos, oos]), "optimal")
    assert isinstance(fr, FoldResult)
    assert fr.fold_id == 0
