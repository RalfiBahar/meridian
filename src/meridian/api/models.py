"""Pydantic response models for the Meridian REST API (Phase 7)."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Market endpoints
# ---------------------------------------------------------------------------


class MarketSummary(BaseModel):
    id: UUID
    external_id: str
    venue: str
    question: str
    category: str
    resolution_status: str
    closes_at: datetime | None
    p_bid: float | None
    p_ask: float | None
    p_mid: float | None
    microprice: float | None


class MarketsResponse(BaseModel):
    markets: list[MarketSummary]
    total: int


class MarketSignalsModel(BaseModel):
    p_bid: float | None
    p_ask: float | None
    p_mid: float | None
    microprice: float | None
    depth_weighted_prob: float | None
    effective_spread: float | None
    obi: float | None
    kyle_lambda: float | None
    amihud: float | None


class TickRow(BaseModel):
    event_ts: datetime
    sequence_no: int
    kind: str
    bid: float | None
    ask: float | None
    bid_size: float | None
    ask_size: float | None
    trade_price: float | None
    trade_size: float | None
    # book_delta fields (from payload jsonb)
    side: str | None = None
    book_price: float | None = None
    book_delta: float | None = None


class BookLevel(BaseModel):
    side: str
    level: int
    price: float
    size: float


class MarketDetail(BaseModel):
    id: UUID
    external_id: str
    venue: str
    question: str
    category: str
    resolution_status: str
    closes_at: datetime | None
    signals: MarketSignalsModel
    book: list[BookLevel]
    recent_ticks: list[TickRow]


# ---------------------------------------------------------------------------
# Arb endpoints
# ---------------------------------------------------------------------------


class PartitionViolation(BaseModel):
    group_id: UUID
    group_label: str
    n_contracts: int
    violation_bps: float
    direction: str
    depth_feasible: bool
    min_ask_sum: float
    max_bid_sum: float


class CrossVenueDivergence(BaseModel):
    group_id: UUID
    venue_a: str
    p_mid_a: float
    venue_b: str
    p_mid_b: float
    divergence_bps: float


class ArbViolationsResponse(BaseModel):
    partition_violations: list[PartitionViolation]
    cross_venue_divergences: list[CrossVenueDivergence]


# ---------------------------------------------------------------------------
# Calibration endpoint
# ---------------------------------------------------------------------------


class ReliabilityBinModel(BaseModel):
    lower: float
    upper: float
    mean_predicted: float
    mean_realized: float
    count: int


class CalibrationResponse(BaseModel):
    category: str | None
    n_markets: int
    n_observations: int
    brier_score: float
    log_loss: float
    brier_after_isotonic: float | None
    reliability_bins: list[ReliabilityBinModel]


# ---------------------------------------------------------------------------
# FedWatch endpoint
# ---------------------------------------------------------------------------


class FedPMFModel(BaseModel):
    fomc_date: date
    source: str
    expected_rate: float
    entropy: float
    strikes: list[float]
    probabilities: list[float]
    raw_p_mid: list[float]


class FedWatchResponse(BaseModel):
    fomc_date: date
    kalshi: FedPMFModel | None
    cme: FedPMFModel | None
