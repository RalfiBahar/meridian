"""Anomaly detector: Isolation Forest over the signals stream.

Identifies statistically unusual market observations using combinations
of microstructure signals (microprice, effective_spread, obi, kyle_lambda,
amihud) from the `signals` table.

Observations are grouped into hourly buckets per market.  Missing features
within a bucket are median-imputed across all observations before the
IsolationForest is fitted.  The decision-function score (lower = more
anomalous; negative = flagged as anomaly) is optionally written back to the
`signals` table as `anomaly_score` rows.
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
from numpy.typing import NDArray
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

_DEFAULT_WINDOW = timedelta(days=7)
_SIGNAL_FEATURES = ("microprice", "effective_spread", "obi", "kyle_lambda", "amihud")
_MIN_OBSERVATIONS = 10


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class AnomalyPoint:
    """One scored hourly observation for a market."""

    ts: datetime
    market_id: UUID
    features: dict[str, float]
    score: float  # IsolationForest.decision_function; lower = more anomalous
    is_anomaly: bool  # True when score < 0 (i.e., IsolationForest predicts -1)


@dataclass
class AnomalyReport:
    """Anomaly detection results for one or more markets over a time window."""

    market_ids: list[UUID]
    window: timedelta
    contamination: float
    n_observations: int
    n_anomalies: int
    points: list[AnomalyPoint] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            "Anomaly Detection — Isolation Forest",
            f"Window:          {self.window.days} days",
            f"Contamination:   {self.contamination:.1%}",
            f"Observations:    {self.n_observations}",
            f"Anomalies:       {self.n_anomalies}",
        ]
        anomalies = sorted(
            [p for p in self.points if p.is_anomaly],
            key=lambda p: p.score,
        )
        if anomalies:
            lines.append("\n— Most anomalous observations (lowest score first) —")
            for pt in anomalies[:10]:
                feat_parts = [f"{k}={v:.4f}" for k, v in pt.features.items() if not np.isnan(v)]
                lines.append(
                    f"  [{pt.ts.strftime('%Y-%m-%d %H:%M')}]"
                    f"  score={pt.score:.3f}"
                    f"  {' '.join(feat_parts)}"
                )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def detect_anomalies(
    pool: asyncpg.Pool,
    *,
    market_id: UUID | None = None,
    window: timedelta = _DEFAULT_WINDOW,
    contamination: float = 0.05,
) -> AnomalyReport | None:
    """Run Isolation Forest over recent microstructure signals.

    If `market_id` is None, pools signals across all open markets.
    Returns None when fewer than _MIN_OBSERVATIONS valid observations exist.
    """
    if market_id is not None:
        market_ids: list[UUID] = [market_id]
    else:
        market_ids = await _open_market_ids(pool)
    if not market_ids:
        return None

    since = datetime.now(tz=UTC) - window
    rows = await _fetch_signals(pool, market_ids, since)

    matrix, meta = _build_feature_matrix(rows)
    if len(matrix) < _MIN_OBSERVATIONS:
        return None

    # Median-impute NaN per column so no observation is silently dropped.
    col_medians: NDArray[np.float64] = np.nanmedian(matrix, axis=0)
    nan_mask = np.isnan(matrix)
    for j in range(matrix.shape[1]):
        matrix[nan_mask[:, j], j] = col_medians[j]

    scaler = StandardScaler()
    X: NDArray[np.float64] = scaler.fit_transform(matrix)

    iso = IsolationForest(contamination=contamination, random_state=42)
    iso.fit(X)
    scores: NDArray[np.float64] = iso.decision_function(X)
    labels: NDArray[np.intp] = iso.predict(X)

    points: list[AnomalyPoint] = []
    for i, m in enumerate(meta):
        raw_feats = {name: float(matrix[i, j]) for j, name in enumerate(_SIGNAL_FEATURES)}
        points.append(
            AnomalyPoint(
                ts=m["ts"],
                market_id=m["market_id"],
                features=raw_feats,
                score=float(scores[i]),
                is_anomaly=(int(labels[i]) == -1),
            )
        )

    return AnomalyReport(
        market_ids=market_ids,
        window=window,
        contamination=contamination,
        n_observations=len(matrix),
        n_anomalies=int((labels == -1).sum()),
        points=points,
    )


async def write_anomaly_signals(pool: asyncpg.Pool, report: AnomalyReport) -> int:
    """Persist anomaly_score rows for all points in the report.

    Returns the number of rows passed to executemany.
    """
    now = datetime.now(tz=UTC)
    meta = json.dumps({"contamination": report.contamination, "window_days": report.window.days})
    records = [(p.ts, p.market_id, "anomaly_score", p.score, meta, now) for p in report.points]
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


def _build_feature_matrix(
    rows: list[dict[str, Any]],
) -> tuple[NDArray[np.float64], list[dict[str, Any]]]:
    """Pivot signal rows into an hourly (market_id, hour) feature matrix.

    Each unique (market_id, 1-hour bucket) becomes one matrix row.  Within
    a bucket, the latest value of each signal type overwrites earlier ones
    (rows arrive in ascending event_ts order).  Features absent from a bucket
    are encoded as NaN.  Buckets where every feature is NaN are dropped.

    Returns:
        matrix  shape (N, F) float64
        meta    parallel list of {"ts": datetime, "market_id": UUID}
    """
    # (market_id_str, hour_datetime) → {signal_type: latest_value}
    buckets: dict[tuple[str, datetime], dict[str, float]] = defaultdict(dict)
    bucket_ts: dict[tuple[str, datetime], datetime] = {}

    for row in rows:
        sig = str(row["signal_type"])
        if sig not in _SIGNAL_FEATURES:
            continue
        mid = str(row["market_id"])
        ts: datetime = row["event_ts"]
        hour = ts.replace(minute=0, second=0, microsecond=0)
        key = (mid, hour)
        # Overwrite so the last (latest) value in the bucket wins.
        buckets[key][sig] = float(row["value"])
        if key not in bucket_ts or ts > bucket_ts[key]:
            bucket_ts[key] = ts

    matrix_rows: list[list[float]] = []
    meta_list: list[dict[str, Any]] = []

    for key in sorted(buckets, key=lambda k: k[1]):
        mid_str, _hour = key
        feats = buckets[key]
        row_vals = [feats.get(f, float("nan")) for f in _SIGNAL_FEATURES]
        if all(np.isnan(v) for v in row_vals):
            continue
        matrix_rows.append(row_vals)
        meta_list.append({"ts": bucket_ts[key], "market_id": UUID(mid_str)})

    if not matrix_rows:
        return np.empty((0, len(_SIGNAL_FEATURES)), dtype=np.float64), []

    return np.array(matrix_rows, dtype=np.float64), meta_list


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
            list(_SIGNAL_FEATURES),
            since,
        )
    return [dict(r) for r in rows]


async def _open_market_ids(pool: asyncpg.Pool) -> list[UUID]:
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT id FROM markets WHERE resolution_status = 'open'")
    return [UUID(str(r["id"])) for r in rows]
