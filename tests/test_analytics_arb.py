"""Unit tests for analytics.arb: no-arbitrage partition checker.

Pure-function tests only — no DB required.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest

from meridian.analytics.arb import KALSHI_FEE_PER_SIDE, PartitionArbResult, _check_partition_arb

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
