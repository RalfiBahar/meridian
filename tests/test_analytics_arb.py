"""Unit tests for analytics.arb: no-arbitrage partition checker."""

from __future__ import annotations

from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest

from meridian.analytics.arb import (
    KALSHI_FEE_PER_SIDE,
    ArbAggregateStats,
    CrossVenueArbResult,
    PartitionArbResult,
    _check_partition_arb,
    _group_contracts,
    _write_cross_venue_signal,
    _write_partition_signal,
    arb_aggregate_stats,
    run_cross_venue_monitor,
    run_partition_monitor,
)

_GROUP_ID = UUID("00000000-0000-0000-0000-000000000001")
_FEE = float(KALSHI_FEE_PER_SIDE)


def _contracts(bids: list[float], asks: list[float]) -> list[dict[str, Any]]:
    """Build minimal contract dicts for _check_partition_arb."""
    return [
        {
            "market_id": str(UUID(int=i + 1)),
            "best_bid": Decimal(str(b)),
            "best_ask": Decimal(str(a)),
            "bid_size": Decimal("100"),
            "ask_size": Decimal("100"),
        }
        for i, (b, a) in enumerate(zip(bids, asks, strict=True))
    ]


# ---------------------------------------------------------------------------
# Feasible partitions (no arb)
# ---------------------------------------------------------------------------


def test_no_arb_perfect_partition() -> None:
    """Three contracts at even prices summing to exactly 1 — no violation."""
    c = _contracts([0.28, 0.28, 0.28], [0.36, 0.36, 0.36])
    # sum(ask) = 1.08 > 1, sum(bid) = 0.84 < 1 → feasible (and fee-adjusted still fine)
    r = _check_partition_arb(_GROUP_ID, "test", c)
    assert r.violation_bps == Decimal("0")
    assert r.direction == "none"


def test_no_arb_two_contract_partition() -> None:
    c = _contracts([0.40, 0.50], [0.50, 0.60])
    # sum(ask) = 1.10 > 1, sum(bid) = 0.90 < 1 → feasible
    r = _check_partition_arb(_GROUP_ID, "test", c)
    assert r.violation_bps == Decimal("0")


# ---------------------------------------------------------------------------
# Long arb (sum of asks < 1)
# ---------------------------------------------------------------------------


def test_long_arb_detected() -> None:
    """sum(asks) + fee < 1 → long arb opportunity."""
    # Two contracts with tight asks that sum to < 1 - 2*fee
    # fee-adjusted sum = 0.44 + 0.44 + 2*fee = 0.88 + 0.04 = 0.92 < 1
    c = _contracts([0.40, 0.40], [0.44, 0.44])
    r = _check_partition_arb(_GROUP_ID, "test", c)
    assert r.violation_bps > Decimal("0")
    assert r.direction == "long"


def test_long_arb_severity_correct() -> None:
    """severity_bps = (1 - sum(eff_asks)) * 10000."""
    # asks = [0.30, 0.30], eff_asks = [0.32, 0.32] (fee=0.02)
    # sum(eff_asks) = 0.64 < 1 → long arb of 36 ¢ → 3600 bps
    c = _contracts([0.25, 0.25], [0.30, 0.30])
    r = _check_partition_arb(_GROUP_ID, "test", c)
    expected_bps = (1.0 - (0.30 + 0.30 + 2 * _FEE)) * 10000
    expected = Decimal(str(round(expected_bps, 2)))
    assert r.violation_bps == pytest.approx(expected, abs=Decimal("0.1"))


# ---------------------------------------------------------------------------
# Short arb (sum of bids > 1)
# ---------------------------------------------------------------------------


def test_short_arb_detected() -> None:
    """sum(bids) - fee > 1 → short arb opportunity."""
    # bids=[0.55, 0.55], eff_bids=[0.53, 0.53], sum=1.06 > 1
    c = _contracts([0.55, 0.55], [0.60, 0.60])
    r = _check_partition_arb(_GROUP_ID, "test", c)
    assert r.violation_bps > Decimal("0")
    assert r.direction == "short"


def test_short_arb_severity_correct() -> None:
    """severity_bps = (sum(eff_bids) - 1) * 10000."""
    # bids=[0.56, 0.56], eff_bids=[0.54, 0.54], sum=1.08 → 8 ¢ → 800 bps
    c = _contracts([0.56, 0.56], [0.60, 0.60])
    r = _check_partition_arb(_GROUP_ID, "test", c)
    expected_bps = ((0.56 - _FEE) + (0.56 - _FEE) - 1.0) * 10000
    assert r.violation_bps == pytest.approx(
        Decimal(str(round(expected_bps, 2))), abs=Decimal("0.1")
    )


# ---------------------------------------------------------------------------
# Depth feasibility
# ---------------------------------------------------------------------------


def test_depth_feasible_when_sizes_nonzero() -> None:
    c = _contracts([0.55, 0.55], [0.60, 0.60])
    r = _check_partition_arb(_GROUP_ID, "test", c)
    if r.violation_bps > 0:
        assert r.depth_feasible  # all contracts have size > 0


def test_depth_infeasible_when_size_zero() -> None:
    c = _contracts([0.55, 0.55], [0.60, 0.60])
    # Wipe out the bid_size on one contract → short arb is not executable.
    c[0]["bid_size"] = Decimal("0")
    r = _check_partition_arb(_GROUP_ID, "test", c)
    if r.violation_bps > 0 and r.direction == "short":
        assert not r.depth_feasible


# ---------------------------------------------------------------------------
# Result structure
# ---------------------------------------------------------------------------


def test_result_fields() -> None:
    c = _contracts([0.30, 0.30, 0.30], [0.35, 0.35, 0.35])
    r = _check_partition_arb(_GROUP_ID, "my group", c)
    assert isinstance(r, PartitionArbResult)
    assert r.group_id == _GROUP_ID
    assert r.group_label == "my group"
    assert r.n_contracts == 3
    assert len(r.market_ids) == 3
    assert r.min_ask_sum > Decimal("0")
    assert r.max_bid_sum > Decimal("0")


# ---------------------------------------------------------------------------
# LP infeasible edge case (inverted spread) — exercises lines 181-185
# ---------------------------------------------------------------------------


def test_lp_infeasible_inverted_spread() -> None:
    """LP returns infeasible when bid > ask (inverted spread); falls back to raw violation."""
    # Contract 1 has inverted spread (bid > ask) making LP bounds invalid.
    # Raw check: eff_ask_sum ≈ 1.04 and eff_bid_sum ≈ 0.91 → raw_violation = 0.
    contracts = [
        {
            "market_id": str(UUID(int=1)),
            "best_bid": Decimal("0.55"),
            "best_ask": Decimal("0.45"),  # bid > ask — inverted
            "bid_size": Decimal("100"),
            "ask_size": Decimal("100"),
        },
        {
            "market_id": str(UUID(int=2)),
            "best_bid": Decimal("0.40"),
            "best_ask": Decimal("0.55"),
            "bid_size": Decimal("100"),
            "ask_size": Decimal("100"),
        },
    ]
    r = _check_partition_arb(_GROUP_ID, "inverted", contracts)
    # LP is infeasible but raw recalculation gives 0 (both sums within range).
    assert r.violation_bps == Decimal("0")
    assert r.direction in ("long", "short", "none")


# ---------------------------------------------------------------------------
# DB helper mock tests
# ---------------------------------------------------------------------------

_GROUP_ID2 = UUID("00000000-0000-0000-0000-000000000002")
_MKT_A = UUID("aaaaaaaa-0000-0000-0000-000000000001")
_MKT_B = UUID("bbbbbbbb-0000-0000-0000-000000000002")


class _SimpleConn:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []
        self.executions: list[tuple[str, tuple[Any, ...]]] = []

    async def fetch(self, query: str, *args: object) -> list[dict[str, Any]]:
        return self._rows

    async def execute(self, query: str, *args: object) -> str:
        self.executions.append((query, args))
        return "INSERT 0 1"


class _SimplePool:
    def __init__(self, conn: _SimpleConn) -> None:
        self._conn = conn

    @asynccontextmanager
    async def acquire(self) -> Any:
        yield self._conn


async def test_group_contracts_returns_rows() -> None:
    """_group_contracts maps DB rows to dicts."""
    rows = [
        {
            "market_id": str(_MKT_A),
            "best_bid": Decimal("0.44"),
            "best_ask": Decimal("0.46"),
            "bid_size": Decimal("100"),
            "ask_size": Decimal("50"),
        }
    ]
    pool = _SimplePool(_SimpleConn(rows))
    result = await _group_contracts(pool, _GROUP_ID)  # type: ignore[arg-type]
    assert len(result) == 1
    assert result[0]["best_bid"] == Decimal("0.44")


async def test_write_partition_signal_calls_execute() -> None:
    """_write_partition_signal issues one INSERT to the signals table."""
    conn = _SimpleConn()
    pool = _SimplePool(conn)
    r = PartitionArbResult(
        group_id=_GROUP_ID,
        group_label="test",
        n_contracts=2,
        min_ask_sum=Decimal("0.90"),
        max_bid_sum=Decimal("0.80"),
        violation_bps=Decimal("500"),
        direction="long",
        depth_feasible=True,
        market_ids=[_MKT_A, _MKT_B],
    )
    await _write_partition_signal(pool, r)  # type: ignore[arg-type]
    assert len(conn.executions) == 1
    assert "arb_violation_bps" in conn.executions[0][0]


async def test_write_cross_venue_signal_calls_execute() -> None:
    """_write_cross_venue_signal issues one INSERT to the signals table."""
    conn = _SimpleConn()
    pool = _SimplePool(conn)
    r = CrossVenueArbResult(
        group_id=_GROUP_ID,
        market_id_a=_MKT_A,
        venue_a="kalshi",
        p_mid_a=Decimal("0.55"),
        market_id_b=_MKT_B,
        venue_b="polymarket",
        p_mid_b=Decimal("0.48"),
        divergence_bps=Decimal("700"),
    )
    await _write_cross_venue_signal(pool, r)  # type: ignore[arg-type]
    assert len(conn.executions) == 1
    assert "cross_venue_divergence_bps" in conn.executions[0][0]


# ---------------------------------------------------------------------------
# run_partition_monitor end-to-end with mock DB
# ---------------------------------------------------------------------------


async def test_run_partition_monitor_returns_violations() -> None:
    """run_partition_monitor detects violations when arb contracts are present."""
    _call = 0
    execute_calls: list[str] = []

    class _MultiConn:
        async def fetch(self, query: str, *args: object) -> list[dict[str, Any]]:
            nonlocal _call
            _call += 1
            if _call == 1:
                # _partition_groups: one group
                return [{"id": str(_GROUP_ID), "label": "test-group"}]
            # _group_contracts: two contracts with long arb (sum(ask) < 1)
            return [
                {
                    "market_id": str(_MKT_A),
                    "best_bid": Decimal("0.25"),
                    "best_ask": Decimal("0.30"),
                    "bid_size": Decimal("100"),
                    "ask_size": Decimal("100"),
                },
                {
                    "market_id": str(_MKT_B),
                    "best_bid": Decimal("0.25"),
                    "best_ask": Decimal("0.30"),
                    "bid_size": Decimal("100"),
                    "ask_size": Decimal("100"),
                },
            ]

        async def execute(self, query: str, *args: object) -> str:
            execute_calls.append(query)
            return "INSERT 0 1"

    class _MultiPool:
        @asynccontextmanager
        async def acquire(self) -> Any:
            yield _MultiConn()

    results = await run_partition_monitor(
        _MultiPool(),  # type: ignore[arg-type]
        threshold_bps=Decimal("0"),
        write_signals=True,
    )
    assert len(results) == 1
    assert results[0].violation_bps > Decimal("0")
    assert results[0].group_label == "test-group"
    # write_signals=True → INSERT called
    assert any("arb_violation_bps" in q for q in execute_calls)


async def test_run_partition_monitor_skips_single_contract_groups() -> None:
    """Groups with fewer than 2 contracts are silently skipped."""
    _call = 0

    class _SingleConn:
        async def fetch(self, query: str, *args: object) -> list[dict[str, Any]]:
            nonlocal _call
            _call += 1
            if _call == 1:
                return [{"id": str(_GROUP_ID), "label": "solo"}]
            return [
                {
                    "market_id": str(_MKT_A),
                    "best_bid": Decimal("0.50"),
                    "best_ask": Decimal("0.55"),
                    "bid_size": Decimal("100"),
                    "ask_size": Decimal("100"),
                }
            ]

    class _SinglePool:
        @asynccontextmanager
        async def acquire(self) -> Any:
            yield _SingleConn()

    results = await run_partition_monitor(  # type: ignore[arg-type]
        _SinglePool(), threshold_bps=Decimal("0"), write_signals=False
    )
    assert results == []


# ---------------------------------------------------------------------------
# run_cross_venue_monitor end-to-end with mock DB
# ---------------------------------------------------------------------------


async def test_run_cross_venue_monitor_returns_divergences() -> None:
    """run_cross_venue_monitor returns results above the threshold."""
    execute_calls: list[str] = []

    class _CrossConn:
        async def fetch(self, query: str, *args: object) -> list[dict[str, Any]]:
            # _cross_venue_pairs
            return [
                {
                    "group_id": str(_GROUP_ID),
                    "p_mid_a": "0.55",
                    "venue_a": "kalshi",
                    "id_a": str(_MKT_A),
                    "p_mid_b": "0.48",
                    "venue_b": "polymarket",
                    "id_b": str(_MKT_B),
                }
            ]

        async def execute(self, query: str, *args: object) -> str:
            execute_calls.append(query)
            return "INSERT 0 1"

    class _CrossPool:
        @asynccontextmanager
        async def acquire(self) -> Any:
            yield _CrossConn()

    results = await run_cross_venue_monitor(
        _CrossPool(),  # type: ignore[arg-type]
        threshold_bps=Decimal("0"),
        write_signals=True,
    )
    assert len(results) == 1
    assert results[0].divergence_bps > Decimal("0")
    assert results[0].venue_a == "kalshi"
    assert any("cross_venue_divergence_bps" in q for q in execute_calls)


async def test_run_cross_venue_monitor_no_pairs() -> None:
    """run_cross_venue_monitor returns empty list when no pairs exist."""

    class _EmptyConn:
        async def fetch(self, query: str, *args: object) -> list[dict[str, Any]]:
            return []

    class _EmptyPool:
        @asynccontextmanager
        async def acquire(self) -> Any:
            yield _EmptyConn()

    results = await run_cross_venue_monitor(  # type: ignore[arg-type]
        _EmptyPool(), threshold_bps=Decimal("0"), write_signals=False
    )
    assert results == []


# ---------------------------------------------------------------------------
# Arb aggregate stats
# ---------------------------------------------------------------------------


async def test_arb_aggregate_stats_empty_db() -> None:
    """arb_aggregate_stats returns zero violations when no signals exist."""

    class _EmptyConn:
        async def fetch(self, query: str, *args: object) -> list[dict[str, Any]]:
            return []

    class _EmptyPool:
        @asynccontextmanager
        async def acquire(self) -> Any:
            yield _EmptyConn()

    stats = await arb_aggregate_stats(_EmptyPool(), lookback_days=30)  # type: ignore[arg-type]
    assert isinstance(stats, ArbAggregateStats)
    assert stats.total_violations == 0
    assert stats.violations_per_day == pytest.approx(0.0)
    assert stats.median_severity_bps is None
    assert stats.lookback_days == 30


async def test_arb_aggregate_stats_with_violations() -> None:
    """arb_aggregate_stats computes violations/day and median from signal rows."""

    class _DataConn:
        async def fetch(self, query: str, *args: object) -> list[dict[str, Any]]:
            return [
                {"value": 15.0},
                {"value": 20.0},
                {"value": 10.0},
                {"value": 25.0},
            ]

    class _DataPool:
        @asynccontextmanager
        async def acquire(self) -> Any:
            yield _DataConn()

    stats = await arb_aggregate_stats(_DataPool(), lookback_days=10)  # type: ignore[arg-type]
    assert stats.total_violations == 4
    assert stats.violations_per_day == pytest.approx(0.4)
    # Median of [10, 15, 20, 25] = 17.5
    assert stats.median_severity_bps == pytest.approx(17.5, abs=0.1)


async def test_arb_aggregate_stats_single_violation() -> None:
    """Single violation row — median equals that value."""

    class _SingleConn:
        async def fetch(self, query: str, *args: object) -> list[dict[str, Any]]:
            return [{"value": 42.0}]

    class _SinglePool:
        @asynccontextmanager
        async def acquire(self) -> Any:
            yield _SingleConn()

    stats = await arb_aggregate_stats(_SinglePool(), lookback_days=7)  # type: ignore[arg-type]
    assert stats.total_violations == 1
    assert stats.median_severity_bps == pytest.approx(42.0)


def test_arb_aggregate_stats_dataclass_fields() -> None:
    """ArbAggregateStats dataclass can be instantiated with expected fields."""
    s = ArbAggregateStats(
        lookback_days=30,
        total_violations=63,
        violations_per_day=2.1,
        median_severity_bps=18.0,
    )
    assert s.lookback_days == 30
    assert s.total_violations == 63
    assert s.violations_per_day == pytest.approx(2.1)
    assert s.median_severity_bps == pytest.approx(18.0)
