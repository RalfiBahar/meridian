"""Unit tests for analytics.fedwatch — pure functions only.

DB-dependent functions are tested with a mock pool following the same
pattern as test_analytics_microstructure.py.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest

from meridian.analytics.fedwatch import (
    EventResponse,
    FedPMF,
    _mean,
    _parse_cme_response,
    _parse_kxfed_strike,
    _var,
    build_kalshi_pmf,
    compute_event_response,
)

_EVENT_ID = UUID("00000000-0000-0000-0000-000000000001")
_MARKET_ID = UUID("00000000-0000-0000-0000-000000000002")
_FOMC_DATE = date(2026, 6, 26)
_NOW = datetime(2026, 6, 26, 18, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Mock pool helpers
# ---------------------------------------------------------------------------


class _MockConn:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self._single: dict[str, Any] | None = rows[0] if rows else None

    async def fetch(self, query: str, *args: object) -> list[dict[str, Any]]:
        return self._rows

    async def fetchrow(self, query: str, *args: object) -> dict[str, Any] | None:
        return self._single


class _MockPool:
    def __init__(self, conn: _MockConn) -> None:
        self._conn = conn

    @asynccontextmanager
    async def acquire(self) -> Any:
        yield self._conn


# ---------------------------------------------------------------------------
# _parse_kxfed_strike
# ---------------------------------------------------------------------------


def test_parse_kxfed_strike_standard() -> None:
    assert _parse_kxfed_strike("KXFED-26JUN-T3.75") == Decimal("3.75")


def test_parse_kxfed_strike_whole_number() -> None:
    assert _parse_kxfed_strike("KXFED-26JUN-T4") == Decimal("4")


def test_parse_kxfed_strike_missing_prefix() -> None:
    assert _parse_kxfed_strike("KXFED-26JUN-3.75") is None  # no 'T' prefix


def test_parse_kxfed_strike_too_few_parts() -> None:
    # The function requires at least 3 dash-separated parts.
    assert _parse_kxfed_strike("KXFED-T3.75") is None  # only 2 parts → None
    assert _parse_kxfed_strike("KXFED") is None  # only 1 part → None


def test_parse_kxfed_strike_non_numeric() -> None:
    assert _parse_kxfed_strike("KXFED-26JUN-TABC") is None


# ---------------------------------------------------------------------------
# _mean and _var
# ---------------------------------------------------------------------------


def test_mean_basic() -> None:
    assert _mean([1.0, 2.0, 3.0]) == pytest.approx(2.0)


def test_var_basic() -> None:
    # var of [1, 2, 3] = ((1-2)^2 + (2-2)^2 + (3-2)^2) / 2 = 1.0
    assert _var([1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_var_single_element_returns_zero() -> None:
    assert _var([5.0]) == 0.0


def test_var_identical_values() -> None:
    assert _var([3.0, 3.0, 3.0]) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# FedPMF dataclass methods
# ---------------------------------------------------------------------------


def _make_pmf(
    strikes: list[float],
    probs: list[float],
    source: str = "kalshi",
) -> FedPMF:
    return FedPMF(
        fomc_date=_FOMC_DATE,
        strikes=[Decimal(str(s)) for s in strikes],
        probabilities=probs,
        raw_p_mid=probs[:],
        source=source,
    )


def test_fedpmf_expected_rate() -> None:
    pmf = _make_pmf([4.0, 4.25, 4.5], [0.2, 0.5, 0.3])
    expected = 4.0 * 0.2 + 4.25 * 0.5 + 4.5 * 0.3
    assert pmf.expected_rate() == pytest.approx(expected)


def test_fedpmf_entropy_uniform() -> None:
    # Uniform over n outcomes → entropy = log2(n)
    n = 4
    pmf = _make_pmf([3.75, 4.0, 4.25, 4.5], [0.25, 0.25, 0.25, 0.25])
    import math

    assert pmf.entropy() == pytest.approx(math.log2(n))


def test_fedpmf_entropy_deterministic() -> None:
    pmf = _make_pmf([4.0], [1.0])
    assert pmf.entropy() == pytest.approx(0.0)


def test_fedpmf_summary_contains_source() -> None:
    pmf = _make_pmf([4.0, 4.25], [0.6, 0.4])
    summary = pmf.summary()
    assert "kalshi" in summary
    assert "4.00%" in summary or "4.0" in summary


# ---------------------------------------------------------------------------
# build_kalshi_pmf (mock pool)
# ---------------------------------------------------------------------------


async def test_build_kalshi_pmf_empty_returns_none() -> None:
    pool = _MockPool(_MockConn([]))
    result = await build_kalshi_pmf(pool, _FOMC_DATE)  # type: ignore[arg-type]
    assert result is None


async def test_build_kalshi_pmf_single_contract() -> None:
    rows = [
        {"id": _MARKET_ID, "external_id": "KXFED-26JUN-T4.25", "p_mid": 1.0},
    ]
    pool = _MockPool(_MockConn(rows))
    result = await build_kalshi_pmf(pool, _FOMC_DATE)  # type: ignore[arg-type]
    assert result is not None
    assert result.strikes == [Decimal("4.25")]
    assert result.probabilities == pytest.approx([1.0])
    assert result.source == "kalshi"


async def test_build_kalshi_pmf_normalizes() -> None:
    _id = lambda n: UUID(f"00000000-0000-0000-0000-{n:012d}")  # noqa: E731
    rows = [
        {"id": _id(10), "external_id": "KXFED-26JUN-T4.00", "p_mid": 0.6},
        {"id": _id(11), "external_id": "KXFED-26JUN-T4.25", "p_mid": 0.3},
        {"id": _id(12), "external_id": "KXFED-26JUN-T4.50", "p_mid": 0.1},
    ]
    pool = _MockPool(_MockConn(rows))
    result = await build_kalshi_pmf(pool, _FOMC_DATE)  # type: ignore[arg-type]
    assert result is not None
    assert result.strikes == [Decimal("4.00"), Decimal("4.25"), Decimal("4.50")]
    total = sum(result.probabilities)
    assert total == pytest.approx(1.0)
    # Largest mass on 4.00
    assert result.probabilities[0] == pytest.approx(0.6)


async def test_build_kalshi_pmf_skips_bad_tickers() -> None:
    _id = lambda n: UUID(f"00000000-0000-0000-0000-{n:012d}")  # noqa: E731
    rows = [
        {"id": _id(10), "external_id": "KXFED-26JUN-TBAD", "p_mid": 0.5},
        {"id": _id(11), "external_id": "KXFED-26JUN-T4.25", "p_mid": 0.5},
    ]
    pool = _MockPool(_MockConn(rows))
    result = await build_kalshi_pmf(pool, _FOMC_DATE)  # type: ignore[arg-type]
    assert result is not None
    # Only one valid contract
    assert len(result.strikes) == 1
    assert result.strikes[0] == Decimal("4.25")


# ---------------------------------------------------------------------------
# _parse_cme_response (pure function)
# ---------------------------------------------------------------------------


def test_parse_cme_response_no_quotes() -> None:
    assert _parse_cme_response({}, _FOMC_DATE) is None
    assert _parse_cme_response({"quotes": []}, _FOMC_DATE) is None


def test_parse_cme_response_no_matching_month() -> None:
    data = {
        "quotes": [
            {
                "expirationMonth": "JUL",
                "expirationYear": "26",
                "last": "95.0",
            }
        ]
    }
    result = _parse_cme_response(data, _FOMC_DATE)  # _FOMC_DATE is June 26
    # "JUN 26" ≠ "JUL 26"
    assert result is None


def test_parse_cme_response_valid() -> None:
    data = {
        "quotes": [
            {
                "expirationMonth": "JUN",
                "expirationYear": "26",
                "last": "95.75",  # implies rate = 4.25%
            }
        ]
    }
    result = _parse_cme_response(data, _FOMC_DATE)
    assert result is not None
    assert result.source == "cme_fedwatch"
    assert result.fomc_date == _FOMC_DATE
    assert len(result.strikes) == 2
    total = sum(result.probabilities)
    assert total == pytest.approx(1.0)


def test_parse_cme_response_missing_price() -> None:
    data = {
        "quotes": [
            {
                "expirationMonth": "JUN",
                "expirationYear": "26",
                "last": "",
            }
        ]
    }
    assert _parse_cme_response(data, _FOMC_DATE) is None


# ---------------------------------------------------------------------------
# compute_event_response (mock pool — two-query setup)
# ---------------------------------------------------------------------------


class _TwoQueryConn:
    """Mock connection that serves different rows depending on query order."""

    def __init__(
        self,
        event_row: dict[str, Any] | None,
        market_rows: list[dict[str, Any]],
        signal_rows: list[dict[str, Any]],
    ) -> None:
        self._event_row = event_row
        self._market_rows = market_rows
        self._signal_rows = signal_rows
        self._fetch_count = 0

    async def fetchrow(self, query: str, *args: object) -> dict[str, Any] | None:
        return self._event_row

    async def fetch(self, query: str, *args: object) -> list[dict[str, Any]]:
        self._fetch_count += 1
        if self._fetch_count == 1:
            return self._market_rows
        return self._signal_rows


class _TwoQueryPool:
    def __init__(self, conn: _TwoQueryConn) -> None:
        self._conn = conn

    @asynccontextmanager
    async def acquire(self) -> Any:
        yield self._conn


async def test_compute_event_response_not_found() -> None:
    pool = _TwoQueryPool(
        _TwoQueryConn(event_row=None, market_rows=[], signal_rows=[])
    )
    result = await compute_event_response(pool, _EVENT_ID)  # type: ignore[arg-type]
    assert result is None


async def test_compute_event_response_no_markets() -> None:
    event = {
        "id": _EVENT_ID,
        "occurred_at": _NOW,
        "category": "fed",
        "label": "FOMC June 2026",
    }
    pool = _TwoQueryPool(
        _TwoQueryConn(event_row=event, market_rows=[], signal_rows=[])
    )
    result = await compute_event_response(pool, _EVENT_ID)  # type: ignore[arg-type]
    assert result is not None
    assert result.n_markets == 0
    assert result.mean_delta_p is None
    assert result.event_label == "FOMC June 2026"


async def test_compute_event_response_basic() -> None:
    """Two markets with pre and post signals compute correct delta."""

    event = {
        "id": _EVENT_ID,
        "occurred_at": _NOW,
        "category": "fed",
        "label": "FOMC June 2026",
    }
    markets = [
        {"id": _MARKET_ID, "external_id": "KXFED-26JUN-T4.25", "ticker": "KXFED-26JUN-T4.25"},
    ]
    # First fetch = markets; subsequent fetches alternate pre/post signals.
    # We need to mock a connection that returns different results for each fetch call.

    class _EventRespConn:
        def __init__(self) -> None:
            self._count = 0

        async def fetchrow(self, query: str, *args: object) -> dict[str, Any] | None:
            return event

        async def fetch(self, query: str, *args: object) -> list[dict[str, Any]]:
            self._count += 1
            if self._count == 1:
                return markets
            elif self._count == 2:
                # pre-event signals
                return [{"value": 0.40}, {"value": 0.42}]
            else:
                # post-event signals
                return [{"value": 0.55}, {"value": 0.57}]

    class _EventRespPool:
        def __init__(self) -> None:
            self._conn = _EventRespConn()

        @asynccontextmanager
        async def acquire(self) -> Any:
            yield self._conn

    result = await compute_event_response(
        _EventRespPool(),  # type: ignore[arg-type]
        _EVENT_ID,  # type: ignore[arg-type]
        pre_window=timedelta(minutes=30),
        post_window=timedelta(minutes=30),
    )
    assert result is not None
    assert result.n_markets == 1
    assert result.mean_delta_p is not None
    # pre_mean = 0.41, post_mean = 0.56, delta = 0.15
    assert result.mean_delta_p == pytest.approx(0.15, abs=1e-9)


# ---------------------------------------------------------------------------
# EventResponse.summary
# ---------------------------------------------------------------------------


def test_event_response_summary_format() -> None:
    er = EventResponse(
        event_id=_EVENT_ID,
        event_label="FOMC June 2026",
        occurred_at=_NOW,
        category="fed",
        pre_window=timedelta(hours=1),
        post_window=timedelta(hours=1),
        n_markets=2,
        mean_delta_p=0.05,
        variance_ratio=1.2,
        per_market=[
            {
                "market_id": _MARKET_ID,
                "ticker": "KXFED-26JUN-T4.25",
                "delta_p": 0.05,
                "pre_mean": 0.40,
                "post_mean": 0.45,
            }
        ],
    )
    s = er.summary()
    assert "FOMC June 2026" in s
    assert "+0.0500" in s or "0.05" in s
    assert "1.200" in s or "1.2" in s
