"""Calibration REST endpoint (Phase 7).

GET /api/v1/calibration  — Brier score, log loss, reliability diagram by category.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query

from meridian.analytics.calibration import CalibrationResult
from meridian.api.auth import require_api_key
from meridian.api.deps import get_pool
from meridian.api.models import CalibrationResponse, ReliabilityBinModel

router = APIRouter()


@router.get("/calibration", response_model=CalibrationResponse)
async def calibration(
    category: str | None = Query(None, description="Market category filter (e.g. 'fed')"),
    lookback: int | None = Query(None, ge=1, description="Lookback window in days"),
    bins: int = Query(10, ge=2, le=50),
    evaluation: Literal["in_sample_ticks", "temporal"] = Query(
        "in_sample_ticks",
        description=(
            "in_sample_ticks scores every p_mid on the same rows used to fit "
            "isotonic regression. temporal scores one pre-settlement forecast "
            "per market and fits isotonic only on earlier settlements."
        ),
    ),
    evaluation_start: datetime | None = Query(
        None,
        description=(
            "Required for evaluation=temporal. Hold out markets settled at or after this time."
        ),
    ),
    pool: asyncpg.Pool = Depends(get_pool),
    _auth: str = Depends(require_api_key),
) -> CalibrationResponse:
    """Return calibration metrics for resolved markets in the given category."""
    from meridian.analytics.calibration import run_calibration, run_temporal_calibration

    if evaluation == "temporal":
        if evaluation_start is None:
            raise HTTPException(
                status_code=422,
                detail="evaluation=temporal requires evaluation_start.",
            )
        if evaluation_start.tzinfo is None:
            raise HTTPException(status_code=422, detail="evaluation_start must include a timezone.")
        result = await run_temporal_calibration(
            pool,
            evaluation_start=evaluation_start,
            category=category,
            n_bins=bins,
        )
    else:
        lb = timedelta(days=lookback) if lookback is not None else None
        result = await run_calibration(pool, category=category, lookback=lb, n_bins=bins)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="No resolved markets with p_mid signals found for the given filters.",
        )

    return _response(result)


@router.get("/calibration/summary")
async def calibration_summary(
    category: str | None = Query(None),
    lookback: int = Query(30, ge=1, description="Lookback window in days"),
    pool: asyncpg.Pool = Depends(get_pool),
    _auth: str = Depends(require_api_key),
) -> CalibrationResponse:
    """Alias for /calibration with a default 30-day rolling window — used for drift charts."""
    from meridian.analytics.calibration import run_calibration

    lb = timedelta(days=lookback)
    result = await run_calibration(pool, category=category, lookback=lb)
    if result is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="No resolved markets found.")

    return _response(result)


def _response(result: CalibrationResult) -> CalibrationResponse:
    return CalibrationResponse(
        category=result.category,
        n_markets=result.n_markets,
        n_observations=result.n_observations,
        brier_score=result.brier_score,
        log_loss=result.log_loss,
        ece=result.ece,
        brier_after_isotonic=result.brier_after_isotonic,
        reliability_bins=[
            ReliabilityBinModel(
                lower=b.lower,
                upper=b.upper,
                mean_predicted=b.mean_predicted,
                mean_realized=b.mean_realized,
                count=b.count,
            )
            for b in result.reliability_bins
        ],
        evaluation_method=result.evaluation_method,
        observation_unit=result.observation_unit,
        isotonic_method=result.isotonic_method,
        brier_isotonic_in_sample=result.brier_isotonic_in_sample,
        n_train_markets=result.n_train_markets,
        n_test_markets=result.n_test_markets,
    )
