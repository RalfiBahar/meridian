"""Unit tests for analytics.regime — Gaussian HMM regime detector.

All tests are pure or use in-process mock pools; no Docker required.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import numpy as np

from meridian.analytics.regime import (
    _REGIME_NAMES,
    RegimePoint,
    RegimeResult,
    _build_sequences,
    detect_regimes,
    write_regime_signals,
)

_MID1 = UUID("00000000-0000-0000-0000-000000000011")
_MID2 = UUID("00000000-0000-0000-0000-000000000022")
_NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)

# ---------------------------------------------------------------------------
# Mock pool infrastructure
# ---------------------------------------------------------------------------


class _MockConn:
    def __init__(
        self,
        market_rows: list[dict[str, Any]] | None = None,
        signal_rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self._market_rows = market_rows or []
        self._signal_rows = signal_rows or []
        self.executemany_calls: list[tuple[str, list[Any]]] = []

    async def fetch(self, query: str, *args: object) -> list[dict[str, Any]]:
        if "signal_type" in query:
            return self._signal_rows
        return self._market_rows

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
# _build_sequences — pure function tests
# ---------------------------------------------------------------------------


def _regime_row(mid: UUID, sig: str, value: float, hour: int = 0) -> dict[str, Any]:
    ts = _NOW.replace(hour=hour % 24)
    return {"event_ts": ts, "market_id": mid, "signal_type": sig, "value": value}


def test_build_sequences_empty_input() -> None:
    seq_map, ts_map = _build_sequences([])
    assert seq_map == {}
    assert ts_map == {}


def test_build_sequences_single_market_single_bucket() -> None:
    rows = [
        _regime_row(_MID1, "effective_spread", 0.03, 0),
        _regime_row(_MID1, "obi", 0.2, 0),
    ]
    seq_map, ts_map = _build_sequences(rows)
    assert _MID1 in seq_map
    assert seq_map[_MID1].shape == (1, 2)
    assert abs(seq_map[_MID1][0, 0] - 0.03) < 1e-9  # effective_spread
    assert abs(seq_map[_MID1][0, 1] - 0.2) < 1e-9  # obi
    assert len(ts_map[_MID1]) == 1


def test_build_sequences_two_markets() -> None:
    rows = [
        _regime_row(_MID1, "effective_spread", 0.02, 0),
        _regime_row(_MID1, "obi", 0.1, 0),
        _regime_row(_MID2, "effective_spread", 0.05, 1),
        _regime_row(_MID2, "obi", -0.1, 1),
    ]
    seq_map, _ = _build_sequences(rows)
    assert _MID1 in seq_map
    assert _MID2 in seq_map
    assert seq_map[_MID1].shape == (1, 2)
    assert seq_map[_MID2].shape == (1, 2)


def test_build_sequences_ignores_non_regime_features() -> None:
    rows = [
        _regime_row(_MID1, "microprice", 0.5, 0),  # not a regime feature
        _regime_row(_MID1, "effective_spread", 0.03, 0),
    ]
    seq_map, _ = _build_sequences(rows)
    # Only effective_spread contributes; obi is missing → partial row with NaN
    assert _MID1 in seq_map
    assert seq_map[_MID1].shape == (1, 2)
    assert np.isnan(seq_map[_MID1][0, 1])  # obi is NaN


def test_build_sequences_all_nan_row_dropped() -> None:
    rows = [{"event_ts": _NOW, "market_id": _MID1, "signal_type": "p_mid", "value": 0.5}]
    seq_map, _ts_map = _build_sequences(rows)
    assert seq_map == {}


def test_build_sequences_ordering_by_time() -> None:
    """Sequence rows are ordered chronologically."""
    rows = [
        _regime_row(_MID1, "effective_spread", 0.05, 2),  # later hour
        _regime_row(_MID1, "effective_spread", 0.02, 0),  # earlier hour
        _regime_row(_MID1, "obi", 0.1, 0),
        _regime_row(_MID1, "obi", 0.3, 2),
    ]
    seq_map, ts_map = _build_sequences(rows)
    assert seq_map[_MID1].shape == (2, 2)
    # First row should be hour=0 (earlier)
    assert ts_map[_MID1][0].hour == 0
    assert abs(seq_map[_MID1][0, 0] - 0.02) < 1e-9
    # Second row should be hour=2 (later)
    assert ts_map[_MID1][1].hour == 2
    assert abs(seq_map[_MID1][1, 0] - 0.05) < 1e-9


# ---------------------------------------------------------------------------
# detect_regimes — async mock-pool tests
# ---------------------------------------------------------------------------


def _make_regime_rows(n: int, mid: UUID = _MID1, base_spread: float = 0.03) -> list[dict[str, Any]]:
    """Generate n hourly rows with effective_spread and obi for one market."""
    rows = []
    rng = np.random.default_rng(42)
    for i in range(n):
        ts = _NOW.replace(hour=i % 24, day=_NOW.day + i // 24)
        rows.append(
            {
                "event_ts": ts,
                "market_id": mid,
                "signal_type": "effective_spread",
                "value": float(base_spread + 0.001 * rng.standard_normal()),
            }
        )
        rows.append(
            {
                "event_ts": ts,
                "market_id": mid,
                "signal_type": "obi",
                "value": float(0.1 * rng.standard_normal()),
            }
        )
    return rows


async def test_detect_regimes_returns_none_when_no_markets() -> None:
    conn = _MockConn(market_rows=[], signal_rows=[])
    pool = _MockPool(conn)
    result = await detect_regimes(pool)  # type: ignore[arg-type]
    assert result is None


async def test_detect_regimes_returns_none_when_too_few_obs() -> None:
    conn = _MockConn(
        market_rows=[{"id": _MID1}],
        signal_rows=_make_regime_rows(5),  # only 5 hourly buckets < 20 minimum
    )
    pool = _MockPool(conn)
    result = await detect_regimes(pool)  # type: ignore[arg-type]
    assert result is None


async def test_detect_regimes_returns_result_with_sufficient_obs() -> None:
    conn = _MockConn(
        market_rows=[{"id": _MID1}],
        signal_rows=_make_regime_rows(30),
    )
    pool = _MockPool(conn)
    result = await detect_regimes(pool)  # type: ignore[arg-type]
    assert result is not None
    assert result.n_observations == 30
    assert result.n_states == 3
    assert len(result.points) == 30


async def test_detect_regimes_state_names_are_low_medium_high() -> None:
    conn = _MockConn(
        market_rows=[{"id": _MID1}],
        signal_rows=_make_regime_rows(40),
    )
    pool = _MockPool(conn)
    result = await detect_regimes(pool)  # type: ignore[arg-type]
    assert result is not None
    valid_names = set(_REGIME_NAMES)
    for pt in result.points:
        assert pt.state_name in valid_names


async def test_detect_regimes_state_ids_in_range() -> None:
    conn = _MockConn(
        market_rows=[{"id": _MID1}],
        signal_rows=_make_regime_rows(30),
    )
    pool = _MockPool(conn)
    result = await detect_regimes(pool, n_states=3)  # type: ignore[arg-type]
    assert result is not None
    for pt in result.points:
        assert 0 <= pt.state_id < 3


async def test_detect_regimes_2_states() -> None:
    conn = _MockConn(
        market_rows=[{"id": _MID1}],
        signal_rows=_make_regime_rows(30),
    )
    pool = _MockPool(conn)
    result = await detect_regimes(pool, n_states=2)  # type: ignore[arg-type]
    assert result is not None
    assert result.n_states == 2
    for pt in result.points:
        assert pt.state_id in (0, 1)
        assert pt.state_name in ("low", "medium")


async def test_detect_regimes_posterior_probs_valid() -> None:
    conn = _MockConn(
        market_rows=[{"id": _MID1}],
        signal_rows=_make_regime_rows(30),
    )
    pool = _MockPool(conn)
    result = await detect_regimes(pool)  # type: ignore[arg-type]
    assert result is not None
    for pt in result.points:
        assert 0.0 <= pt.prob <= 1.0


async def test_detect_regimes_current_state_is_latest() -> None:
    rows = _make_regime_rows(25)
    conn = _MockConn(market_rows=[{"id": _MID1}], signal_rows=rows)
    pool = _MockPool(conn)
    result = await detect_regimes(pool)  # type: ignore[arg-type]
    assert result is not None
    assert result.current_state == result.points[-1].state_name


async def test_detect_regimes_category_passed_to_query() -> None:
    """When category is supplied, the pool is queried for markets by category."""
    conn = _MockConn(
        market_rows=[{"id": _MID1}],
        signal_rows=_make_regime_rows(25),
    )
    pool = _MockPool(conn)
    result = await detect_regimes(pool, category="fed")  # type: ignore[arg-type]
    assert result is not None
    assert result.category == "fed"


# ---------------------------------------------------------------------------
# write_regime_signals
# ---------------------------------------------------------------------------


async def test_write_regime_signals_calls_executemany() -> None:
    conn = _MockConn()
    pool = _MockPool(conn)
    result = RegimeResult(
        category="fed",
        n_states=3,
        n_observations=2,
        current_state="low",
        points=[
            RegimePoint(ts=_NOW, market_id=_MID1, state_id=0, state_name="low", prob=0.9),
            RegimePoint(
                ts=_NOW.replace(hour=1), market_id=_MID1, state_id=1, state_name="medium", prob=0.7
            ),
        ],
    )
    n = await write_regime_signals(pool, result)  # type: ignore[arg-type]
    assert n == 2
    assert len(conn.executemany_calls) == 1
    _, records = conn.executemany_calls[0]
    assert len(records) == 2
    assert records[0][2] == "regime_state"
    assert records[0][3] == 0.0  # state_id 0 → float 0.0


async def test_write_regime_signals_empty_result() -> None:
    conn = _MockConn()
    pool = _MockPool(conn)
    result = RegimeResult(
        category=None, n_states=3, n_observations=0, current_state=None, points=[]
    )
    n = await write_regime_signals(pool, result)  # type: ignore[arg-type]
    assert n == 0
    assert conn.executemany_calls == []


# ---------------------------------------------------------------------------
# RegimeResult.summary
# ---------------------------------------------------------------------------


def test_regime_result_summary_format() -> None:
    result = RegimeResult(
        category="fed",
        n_states=3,
        n_observations=30,
        current_state="high",
        points=[
            RegimePoint(ts=_NOW, market_id=_MID1, state_id=0, state_name="low", prob=0.8),
            RegimePoint(ts=_NOW, market_id=_MID1, state_id=2, state_name="high", prob=0.9),
        ],
    )
    s = result.summary()
    assert "fed" in s
    assert "3" in s
    assert "30" in s
    assert "high" in s
    assert "low" in s
    assert "%" in s


def test_regime_result_summary_no_category() -> None:
    result = RegimeResult(
        category=None, n_states=2, n_observations=20, current_state="low", points=[]
    )
    s = result.summary()
    assert "(all)" in s
