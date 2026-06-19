"""Regime detector: Gaussian HMM over per-category volatility states.

Fits a k-state Gaussian Hidden Markov Model (hmmlearn GaussianHMM) on
time-ordered sequences of effective_spread and obi signals for markets in
a given category.  Multiple market sequences are fitted jointly using
hmmlearn's multi-sequence API.

States are decoded with the Viterbi algorithm and labeled low / medium /
high volatility by ascending mean effective_spread of each HMM component.
Posterior state probabilities from the forward-backward algorithm are
stored alongside each decoded state.

Results are optionally written to the `signals` table as `regime_state`
rows (value = mapped state index 0/1/2) with label and prob in metadata.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg
import numpy as np
from hmmlearn.hmm import GaussianHMM
from numpy.typing import NDArray

_DEFAULT_WINDOW = timedelta(days=30)
_REGIME_FEATURES = ("effective_spread", "obi")
_MIN_OBSERVATIONS = 20
_REGIME_NAMES = ("low", "medium", "high")


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class RegimePoint:
    """One regime-state assignment for a (market, timestamp) pair."""

    ts: datetime
    market_id: UUID
    state_id: int    # 0 / 1 / 2 sorted by ascending mean effective_spread
    state_name: str  # 'low' / 'medium' / 'high'
    prob: float      # posterior probability of this state from forward-backward


@dataclass
class RegimeResult:
    """Regime detection output for a set of markets."""

    category: str | None
    n_states: int
    n_observations: int
    current_state: str | None  # state_name of the most recent observation
    points: list[RegimePoint] = field(default_factory=list)

    def summary(self) -> str:
        state_counts: dict[str, int] = defaultdict(int)
        for p in self.points:
            state_counts[p.state_name] += 1
        n = max(1, len(self.points))
        lines = [
            "Regime Detection — Gaussian HMM",
            f"Category:        {self.category or '(all)'}",
            f"States:          {self.n_states}",
            f"Observations:    {self.n_observations}",
            f"Current regime:  {self.current_state or 'n/a'}",
            "\n— State frequencies —",
        ]
        for name in _REGIME_NAMES[: self.n_states]:
            pct = 100 * state_counts.get(name, 0) / n
            lines.append(f"  {name:<10} {pct:5.1f}%")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def detect_regimes(
    pool: asyncpg.Pool,
    *,
    category: str | None = None,
    n_states: int = 3,
    window: timedelta = _DEFAULT_WINDOW,
) -> RegimeResult | None:
    """Fit a Gaussian HMM and decode per-market volatility regime labels.

    If `category` is None, all open markets are used.
    Returns None when fewer than _MIN_OBSERVATIONS valid observations exist
    or when the HMM fails to converge.
    """
    if category is not None:
        market_ids = await _markets_by_category(pool, category)
    else:
        market_ids = await _open_market_ids(pool)
    if not market_ids:
        return None

    since = datetime.now(tz=UTC) - window
    rows = await _fetch_signals(pool, market_ids, since)

    seq_map, ts_map = _build_sequences(rows)
    if not seq_map:
        return None

    # Concatenate all per-market sequences for multi-sequence HMM fitting.
    seq_list = list(seq_map.values())
    lengths = [len(s) for s in seq_list]
    X: NDArray[np.float64] = np.concatenate(seq_list, axis=0)

    if len(X) < _MIN_OBSERVATIONS:
        return None

    # Median-impute NaN per column before fitting.
    col_medians: NDArray[np.float64] = np.nanmedian(X, axis=0)
    for j in range(X.shape[1]):
        nan_idx = np.isnan(X[:, j])
        X[nan_idx, j] = col_medians[j]

    try:
        model = GaussianHMM(
            n_components=n_states,
            covariance_type="diag",
            n_iter=100,
            random_state=42,
        )
        model.fit(X, lengths)
        all_states: NDArray[np.intp] = np.asarray(
            model.predict(X, lengths), dtype=np.intp
        )
        all_probs: NDArray[np.float64] = np.asarray(
            model.predict_proba(X, lengths), dtype=np.float64
        )
    except Exception:
        return None

    # Map HMM state indices to low/medium/high by ascending mean effective_spread.
    means: NDArray[np.float64] = np.asarray(model.means_, dtype=np.float64)
    order: NDArray[np.intp] = np.argsort(means[:, 0]).astype(np.intp)
    # state_map[hmm_state] → sorted_index (0=low, 1=medium, 2=high)
    state_map: dict[int, int] = {int(order[i]): i for i in range(n_states)}
    regime_names = list(_REGIME_NAMES[:n_states])

    # Reconstruct per-market state assignments using cumulative length offsets.
    points: list[RegimePoint] = []
    offset = 0
    for mid, seq in zip(seq_map.keys(), seq_list, strict=False):
        n = len(seq)
        ts_list = ts_map[mid]
        for i in range(n):
            raw_state = int(all_states[offset + i])
            mapped = state_map[raw_state]
            name = regime_names[mapped] if mapped < len(regime_names) else str(mapped)
            posterior = float(all_probs[offset + i, raw_state])
            points.append(
                RegimePoint(
                    ts=ts_list[i],
                    market_id=mid,
                    state_id=mapped,
                    state_name=name,
                    prob=posterior,
                )
            )
        offset += n

    current_state = points[-1].state_name if points else None

    return RegimeResult(
        category=category,
        n_states=n_states,
        n_observations=len(X),
        current_state=current_state,
        points=points,
    )


async def write_regime_signals(pool: asyncpg.Pool, result: RegimeResult) -> int:
    """Persist regime_state rows for all points in the result.

    Returns the number of rows passed to executemany.
    """
    now = datetime.now(tz=UTC)
    records = [
        (
            p.ts,
            p.market_id,
            "regime_state",
            float(p.state_id),
            json.dumps({"state_name": p.state_name, "prob": p.prob}),
            now,
        )
        for p in result.points
    ]
    if not records:
        return 0
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO signals (event_ts, market_id, signal_type, value, metadata, ingest_ts)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6)
            ON CONFLICT DO NOTHING
            """,
            records,
        )
    return len(records)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _build_sequences(
    rows: list[dict[str, Any]],
) -> tuple[
    dict[UUID, NDArray[np.float64]],
    dict[UUID, list[datetime]],
]:
    """Group signal rows into per-market hourly time-ordered feature sequences.

    Returns (seq_map, ts_map) keyed by market_id.
    seq_map[mid] is a float64 array of shape (N, 2) ordered by time.
    ts_map[mid]  is the parallel list of hourly bucket timestamps.

    Buckets where both features are NaN are dropped.  Within a bucket the
    latest signal value (rows arrive ASC by event_ts) overwrites earlier ones.
    """
    buckets: dict[tuple[str, datetime], dict[str, float]] = defaultdict(dict)
    bucket_ts: dict[tuple[str, datetime], datetime] = {}

    for row in rows:
        sig = str(row["signal_type"])
        if sig not in _REGIME_FEATURES:
            continue
        mid_str = str(row["market_id"])
        ts: datetime = row["event_ts"]
        hour = ts.replace(minute=0, second=0, microsecond=0)
        key = (mid_str, hour)
        buckets[key][sig] = float(row["value"])
        if key not in bucket_ts or ts > bucket_ts[key]:
            bucket_ts[key] = ts

    # Collect per-market sorted (ts, feature_vector) pairs.
    market_data: dict[str, list[tuple[datetime, list[float]]]] = defaultdict(list)
    for (mid_str, _hour), feats in buckets.items():
        row_vals = [feats.get(f, float("nan")) for f in _REGIME_FEATURES]
        if all(np.isnan(v) for v in row_vals):
            continue
        market_data[mid_str].append((bucket_ts[(mid_str, _hour)], row_vals))

    seq_map: dict[UUID, NDArray[np.float64]] = {}
    ts_map: dict[UUID, list[datetime]] = {}

    for mid_str, bucket_list in market_data.items():
        sorted_pairs = sorted(bucket_list, key=lambda x: x[0])
        mid: UUID = UUID(mid_str)
        ts_map[mid] = [p[0] for p in sorted_pairs]
        seq_map[mid] = np.array([p[1] for p in sorted_pairs], dtype=np.float64)

    return seq_map, ts_map


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def _fetch_signals(
    pool: asyncpg.Pool,
    market_ids: list[UUID],
    since: datetime,
) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT event_ts, market_id, signal_type, value
            FROM   signals
            WHERE  market_id   = ANY($1::uuid[])
              AND  signal_type = ANY($2::text[])
              AND  event_ts   >= $3
            ORDER BY event_ts ASC
            """,
            market_ids,
            list(_REGIME_FEATURES),
            since,
        )
    return [dict(r) for r in rows]


async def _markets_by_category(pool: asyncpg.Pool, category: str) -> list[UUID]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id FROM markets
            WHERE  resolution_status = 'open'
              AND  category = $1
            """,
            category,
        )
    return [UUID(str(r["id"])) for r in rows]


async def _open_market_ids(pool: asyncpg.Pool) -> list[UUID]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id FROM markets WHERE resolution_status = 'open'"
        )
    return [UUID(str(r["id"])) for r in rows]
