"""Calibration REST endpoint (Phase 7).

GET /api/v1/calibration  — Brier score, log loss, reliability diagram by category.
"""

from __future__ import annotations

from datetime import timedelta

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query

from meridian.api.auth import require_api_key
from meridian.api.deps import get_pool
from meridian.api.models import CalibrationResponse, ReliabilityBinModel

router = APIRouter()


@router.get("/calibration", response_model=CalibrationResponse)
async def calibration(
    category: str | None = Query(None, description="Market category filter (e.g. 'fed')"),
    lookback: int | None = Query(None, ge=1, description="Lookback window in days"),
    bins: int = Query(10, ge=2, le=50),
    pool: asyncpg.Pool = Depends(get_pool),
    _auth: str = Depends(require_api_key),
) -> CalibrationResponse:
    """Return calibration metrics for resolved markets in the given category."""
    from meridian.analytics.calibration import run_calibration

    lb = timedelta(days=lookback) if lookback is not None else None
    result = await run_calibration(pool, category=category, lookback=lb, n_bins=bins)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="No resolved markets with p_mid signals found for the given filters.",
        )

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
    )


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
    )
