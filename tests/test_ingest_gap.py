"""Unit tests for GapDetector — no real DB required."""

from __future__ import annotations

from typing import Any

from meridian.ingest.gap import GapDetector


def _msg(sid: int, seq: int) -> dict[str, Any]:
    return {"type": "orderbook_delta", "sid": sid, "seq": seq, "msg": {"market_ticker": "T"}}


# ---------------------------------------------------------------------------
# No-gap cases
# ---------------------------------------------------------------------------


async def test_first_message_returns_zero(mock_pool: Any) -> None:
    gd = GapDetector(mock_pool)
    result = await gd.observe(_msg(1, 100))
    assert result == 0
    assert gd.gaps_detected == 0


async def test_sequential_returns_zero(mock_pool: Any) -> None:
    gd = GapDetector(mock_pool)
    for seq in range(100, 110):
        result = await gd.observe(_msg(1, seq))
        assert result == 0
    assert gd.gaps_detected == 0


async def test_independent_sids_no_gap(mock_pool: Any) -> None:
    gd = GapDetector(mock_pool)
    assert await gd.observe(_msg(1, 1)) == 0
    assert await gd.observe(_msg(2, 1)) == 0  # different sid; seq=1 is first
    assert await gd.observe(_msg(1, 2)) == 0
    assert await gd.observe(_msg(2, 2)) == 0


# ---------------------------------------------------------------------------
# Gap detected
# ---------------------------------------------------------------------------


async def test_forward_gap_returns_gap_size(mock_pool: Any, mock_conn: Any) -> None:
    gd = GapDetector(mock_pool)
    await gd.observe(_msg(1, 10))
    gap = await gd.observe(_msg(1, 15))  # gap of 4 (11,12,13,14 missing)
    assert gap == 4
    assert gd.gaps_detected == 1


async def test_forward_gap_emits_signal_row(mock_pool: Any, mock_conn: Any) -> None:
    gd = GapDetector(mock_pool)
    await gd.observe(_msg(1, 1))
    await gd.observe(_msg(1, 5))
    assert len(mock_conn.executions) == 1
    query, _ = mock_conn.executions[0]
    assert "INSERT INTO signals" in query


async def test_gap_of_one_is_detected(mock_pool: Any) -> None:
    gd = GapDetector(mock_pool)
    await gd.observe(_msg(1, 10))
    gap = await gd.observe(_msg(1, 12))  # 11 missing
    assert gap == 1
    assert gd.gaps_detected == 1


async def test_multiple_gaps_counted(mock_pool: Any) -> None:
    gd = GapDetector(mock_pool)
    await gd.observe(_msg(1, 1))
    await gd.observe(_msg(1, 3))   # gap=1
    await gd.observe(_msg(1, 10))  # gap=6
    assert gd.gaps_detected == 2


# ---------------------------------------------------------------------------
# No-op cases (out-of-order, duplicates, missing fields)
# ---------------------------------------------------------------------------


async def test_duplicate_seq_returns_zero(mock_pool: Any) -> None:
    gd = GapDetector(mock_pool)
    await gd.observe(_msg(1, 5))
    result = await gd.observe(_msg(1, 5))
    assert result == 0
    assert gd.gaps_detected == 0


async def test_out_of_order_returns_zero(mock_pool: Any) -> None:
    gd = GapDetector(mock_pool)
    await gd.observe(_msg(1, 10))
    result = await gd.observe(_msg(1, 8))  # earlier seq — not a forward gap
    assert result == 0
    assert gd.gaps_detected == 0


async def test_missing_sid_returns_zero(mock_pool: Any) -> None:
    gd = GapDetector(mock_pool)
    result = await gd.observe({"type": "ticker", "seq": 5, "msg": {}})
    assert result == 0


async def test_missing_seq_returns_zero(mock_pool: Any) -> None:
    gd = GapDetector(mock_pool)
    result = await gd.observe({"type": "ticker", "sid": 1, "msg": {}})
    assert result == 0


async def test_non_int_sid_returns_zero(mock_pool: Any) -> None:
    gd = GapDetector(mock_pool)
    result = await gd.observe({"type": "ticker", "sid": "abc", "seq": 1, "msg": {}})
    assert result == 0


async def test_empty_message_returns_zero(mock_pool: Any) -> None:
    gd = GapDetector(mock_pool)
    result = await gd.observe({})
    assert result == 0


async def test_no_db_call_when_no_gap(mock_pool: Any, mock_conn: Any) -> None:
    gd = GapDetector(mock_pool)
    await gd.observe(_msg(1, 1))
    await gd.observe(_msg(1, 2))
    await gd.observe(_msg(1, 3))
    assert not mock_conn.executions


# ---------------------------------------------------------------------------
# Signal metadata
# ---------------------------------------------------------------------------


async def test_gap_signal_metadata_contains_sid_and_seqs(
    mock_pool: Any, mock_conn: Any
) -> None:
    import json

    gd = GapDetector(mock_pool)
    await gd.observe(_msg(7, 100))
    await gd.observe(_msg(7, 105))
    assert len(mock_conn.executions) == 1
    _, args = mock_conn.executions[0]
    # args[2] is the metadata JSON string (third positional arg after $1 and $2)
    metadata = json.loads(args[2])
    assert metadata["sid"] == 7
    assert metadata["last_seq"] == 100
    assert metadata["new_seq"] == 105
