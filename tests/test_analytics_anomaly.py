"""Unit tests for analytics.anomaly — Isolation Forest anomaly detector.

All tests are pure or use in-process mock pools; no Docker required.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import numpy as np

from meridian.analytics.anomaly import (
    AnomalyPoint,
    AnomalyReport,
    _build_feature_matrix,
    detect_anomalies,
    write_anomaly_signals,
)

_MID1 = UUID("00000000-0000-0000-0000-000000000001")
_MID2 = UUID("00000000-0000-0000-0000-000000000002")
_NOW = datetime(2026, 1, 10, 12, 0, 0, tzinfo=UTC)

# ---------------------------------------------------------------------------
# Mock pool infrastructure
# ---------------------------------------------------------------------------


class _MockConn:
    """Tracks fetch / executemany calls; dispatches by query substring."""

    def __init__(
        self,
        market_rows: list[dict[str, Any]] | None = None,
        signal_rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self._market_rows = market_rows or []
        self._signal_rows = signal_rows or []
        self.executemany_calls: list[tuple[str, list[Any]]] = []
        self._fetch_call_count = 0

    async def fetch(self, query: str, *args: object) -> list[dict[str, Any]]:
        self._fetch_call_count += 1
        if "resolution_status" in query and "signal_type" not in query:
            return self._market_rows
        return self._signal_rows

    async def fetchrow(self, query: str, *args: object) -> dict[str, Any] | None:
        return None

    async def executemany(self, query: str, records: object) -> None:
        self.executemany_calls.append(
            (query, list(records) if hasattr(records, "__iter__") else [records])
        )


class _MockPool:
    def __init__(self, conn: _MockConn) -> None:
        self._conn = conn

    @asynccontextmanager
    async def acquire(self) -> Any:
        yield self._conn


# ---------------------------------------------------------------------------
# _build_feature_matrix — pure function tests
# ---------------------------------------------------------------------------


def _sig_row(
    mid: UUID,
    sig: str,
    value: float,
    offset_hours: int = 0,
) -> dict[str, Any]:
    ts = _NOW.replace(hour=offset_hours % 24)
    return {"event_ts": ts, "market_id": mid, "signal_type": sig, "value": value}


def test_build_feature_matrix_empty_input() -> None:
    matrix, meta = _build_feature_matrix([])
    assert matrix.shape == (0, 5)
    assert meta == []


def test_build_feature_matrix_single_bucket() -> None:
    rows = [
        _sig_row(_MID1, "microprice", 0.50, 0),
        _sig_row(_MID1, "effective_spread", 0.04, 0),
    ]
    matrix, meta = _build_feature_matrix(rows)
    assert matrix.shape == (1, 5)
    assert len(meta) == 1
    assert meta[0]["market_id"] == _MID1
    # microprice is index 0, effective_spread is index 1
    assert abs(matrix[0, 0] - 0.50) < 1e-9
    assert abs(matrix[0, 1] - 0.04) < 1e-9
    # Missing features → NaN
    assert np.isnan(matrix[0, 2])  # obi
    assert np.isnan(matrix[0, 3])  # kyle_lambda
    assert np.isnan(matrix[0, 4])  # amihud


def test_build_feature_matrix_two_markets_two_hours() -> None:
    rows = [
        _sig_row(_MID1, "microprice", 0.50, 0),
        _sig_row(_MID1, "effective_spread", 0.03, 0),
        _sig_row(_MID2, "microprice", 0.70, 1),
        _sig_row(_MID2, "obi", -0.1, 1),
    ]
    matrix, meta = _build_feature_matrix(rows)
    # Each (market, hour) is one row: 2 rows total
    assert matrix.shape == (2, 5)
    assert len(meta) == 2


def test_build_feature_matrix_latest_value_wins() -> None:
    """When two signals with the same (market, hour, type) arrive, the later one wins."""
    ts_early = _NOW.replace(hour=0, minute=10)
    ts_late = _NOW.replace(hour=0, minute=50)
    rows = [
        {"event_ts": ts_early, "market_id": _MID1, "signal_type": "microprice", "value": 0.40},
        {"event_ts": ts_late, "market_id": _MID1, "signal_type": "microprice", "value": 0.60},
    ]
    matrix, _meta = _build_feature_matrix(rows)
    assert matrix.shape == (1, 5)
    assert abs(matrix[0, 0] - 0.60) < 1e-9


def test_build_feature_matrix_drops_all_nan_rows() -> None:
    """Rows with unrecognised signal types produce all-NaN rows, which are dropped."""
    rows = [{"event_ts": _NOW, "market_id": _MID1, "signal_type": "unknown", "value": 99.0}]
    matrix, meta = _build_feature_matrix(rows)
    assert matrix.shape == (0, 5)
    assert meta == []


def test_build_feature_matrix_ignores_unknown_signal_types() -> None:
    """Unknown signal types are skipped; known ones still create a valid row."""
    rows = [
        _sig_row(_MID1, "microprice", 0.55, 0),
        {"event_ts": _NOW, "market_id": _MID1, "signal_type": "p_mid", "value": 0.55},
    ]
    matrix, _meta = _build_feature_matrix(rows)
    assert matrix.shape == (1, 5)
    assert not np.isnan(matrix[0, 0])  # microprice populated


# ---------------------------------------------------------------------------
# detect_anomalies — async mock-pool tests
# ---------------------------------------------------------------------------


def _make_signal_rows(n: int, mid: UUID = _MID1) -> list[dict[str, Any]]:
    """Generate n hourly rows with all 5 features for one market."""
    rows = []
    for i in range(n):
        ts = _NOW.replace(hour=i % 24, day=_NOW.day + i // 24)
        for sig, val in [
            ("microprice", 0.5 + 0.01 * i),
            ("effective_spread", 0.02 + 0.001 * i),
            ("obi", 0.1 * (i % 5)),
            ("kyle_lambda", 0.001),
            ("amihud", 0.0001),
        ]:
            rows.append({"event_ts": ts, "market_id": mid, "signal_type": sig, "value": val})
    return rows


async def test_detect_anomalies_returns_none_when_no_markets() -> None:
    conn = _MockConn(market_rows=[], signal_rows=[])
    pool = _MockPool(conn)
    result = await detect_anomalies(pool)  # type: ignore[arg-type]
    assert result is None


async def test_detect_anomalies_returns_none_when_too_few_obs() -> None:
    conn = _MockConn(
        market_rows=[{"id": _MID1}],
        signal_rows=_make_signal_rows(5),  # < 10 hourly buckets
    )
    pool = _MockPool(conn)
    result = await detect_anomalies(pool)  # type: ignore[arg-type]
    assert result is None


async def test_detect_anomalies_returns_report_with_10_obs() -> None:
    conn = _MockConn(
        market_rows=[{"id": _MID1}],
        signal_rows=_make_signal_rows(10),
    )
    pool = _MockPool(conn)
    result = await detect_anomalies(pool)  # type: ignore[arg-type]
    assert result is not None
    assert result.n_observations == 10
    assert 0 <= result.n_anomalies <= 10
    assert len(result.points) == 10


async def test_detect_anomalies_single_market_arg() -> None:
    """When market_id is supplied, pool does not call _open_market_ids."""
    conn = _MockConn(
        market_rows=[],  # would return empty if queried
        signal_rows=_make_signal_rows(12),
    )
    pool = _MockPool(conn)
    result = await detect_anomalies(pool, market_id=_MID1)  # type: ignore[arg-type]
    assert result is not None
    assert result.market_ids == [_MID1]
    # The open-markets query should NOT have been called.
    assert conn._fetch_call_count == 1  # only the signals query


async def test_detect_anomalies_flags_known_outlier() -> None:
    """An observation with extreme features should be flagged as an anomaly."""
    normal_rows = _make_signal_rows(30)
    # Add a single extreme observation in a different hour bucket
    outlier_ts = datetime(2026, 2, 1, 5, 0, 0, tzinfo=UTC)
    for sig, val in [
        ("microprice", 999.0),  # extreme outlier
        ("effective_spread", 999.0),
        ("obi", 999.0),
        ("kyle_lambda", 999.0),
        ("amihud", 999.0),
    ]:
        normal_rows.append(
            {"event_ts": outlier_ts, "market_id": _MID1, "signal_type": sig, "value": val}
        )

    conn = _MockConn(
        market_rows=[{"id": _MID1}],
        signal_rows=normal_rows,
    )
    pool = _MockPool(conn)
    result = await detect_anomalies(pool, contamination=0.05)  # type: ignore[arg-type]
    assert result is not None
    # Lowest-score point should be the outlier (ts=outlier_ts hour=5)
    lowest = min(result.points, key=lambda p: p.score)
    assert lowest.ts.hour == 5
    assert lowest.is_anomaly


async def test_detect_anomalies_contamination_respected() -> None:
    """contamination=0.0 still classifies ~0 anomalies (IsolationForest clamped)."""
    conn = _MockConn(
        market_rows=[{"id": _MID1}],
        signal_rows=_make_signal_rows(20),
    )
    pool = _MockPool(conn)
    # contamination must be >0 for IsolationForest; use a very small value
    result = await detect_anomalies(pool, contamination=0.01)  # type: ignore[arg-type]
    assert result is not None
    # With 1% contamination on 20 obs, at most 1 flagged
    assert result.n_anomalies <= 1


async def test_detect_anomalies_report_fields() -> None:
    conn = _MockConn(
        market_rows=[{"id": _MID1}],
        signal_rows=_make_signal_rows(15),
    )
    pool = _MockPool(conn)
    result = await detect_anomalies(pool, window=timedelta(days=5), contamination=0.10)  # type: ignore[arg-type]
    assert result is not None
    assert result.window == timedelta(days=5)
    assert result.contamination == 0.10
    assert result.market_ids == [_MID1]


# ---------------------------------------------------------------------------
# write_anomaly_signals
# ---------------------------------------------------------------------------


async def test_write_anomaly_signals_calls_executemany() -> None:
    conn = _MockConn()
    pool = _MockPool(conn)
    report = AnomalyReport(
        market_ids=[_MID1],
        window=timedelta(days=7),
        contamination=0.05,
        n_observations=2,
        n_anomalies=1,
        points=[
            AnomalyPoint(
                ts=_NOW,
                market_id=_MID1,
                features={"microprice": 0.5},
                score=-0.1,
                is_anomaly=True,
            ),
            AnomalyPoint(
                ts=_NOW.replace(hour=1),
                market_id=_MID1,
                features={"microprice": 0.51},
                score=0.2,
                is_anomaly=False,
            ),
        ],
    )
    n = await write_anomaly_signals(pool, report)  # type: ignore[arg-type]
    assert n == 2
    assert len(conn.executemany_calls) == 1
    _, records = conn.executemany_calls[0]
    assert len(records) == 2
    # First record: ts, market_id, "anomaly_score", score, metadata, ingest_ts
    assert records[0][2] == "anomaly_score"
    assert abs(records[0][3] - (-0.1)) < 1e-9


async def test_write_anomaly_signals_empty_report() -> None:
    conn = _MockConn()
    pool = _MockPool(conn)
    report = AnomalyReport(
        market_ids=[],
        window=timedelta(days=7),
        contamination=0.05,
        n_observations=0,
        n_anomalies=0,
        points=[],
    )
    n = await write_anomaly_signals(pool, report)  # type: ignore[arg-type]
    assert n == 0
    assert conn.executemany_calls == []


# ---------------------------------------------------------------------------
# AnomalyReport.summary
# ---------------------------------------------------------------------------


def test_anomaly_report_summary_contains_key_fields() -> None:
    report = AnomalyReport(
        market_ids=[_MID1],
        window=timedelta(days=7),
        contamination=0.05,
        n_observations=20,
        n_anomalies=1,
        points=[
            AnomalyPoint(
                ts=_NOW,
                market_id=_MID1,
                features={"microprice": 0.5, "effective_spread": float("nan")},
                score=-0.15,
                is_anomaly=True,
            )
        ],
    )
    s = report.summary()
    assert "7 days" in s
    assert "5.0%" in s
    assert "20" in s
    assert "1" in s
    assert "score=-0.150" in s


def test_anomaly_report_summary_no_anomalies() -> None:
    report = AnomalyReport(
        market_ids=[_MID1],
        window=timedelta(days=3),
        contamination=0.05,
        n_observations=10,
        n_anomalies=0,
        points=[],
    )
    s = report.summary()
    assert "Anomalies:       0" in s
