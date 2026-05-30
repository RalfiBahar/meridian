"""Sequence-gap detection on raw WS envelopes.

Per-`sid` tracking: when `seq` jumps by more than 1, emit a row in
`signals` so calibration analysis can discount the affected window.

Only the orderbook channels carry monotonic `seq` numbers in our model;
other channels (ticker) have no envelope-level seq and fall back to
`ts_ms` in the normalizer. The detector observes only payloads with a
real `seq` to avoid spurious gap signals on the synthetic ones.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import asyncpg


class GapDetector:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self._last_seq: dict[int, int] = {}
        self.gaps_detected = 0

    async def observe(self, raw: dict[str, Any]) -> int:
        """Inspect one raw WS envelope.

        Returns the gap size (>0 if a gap was detected and a signal emitted,
        else 0). Only operates on messages with both `sid` and `seq`.
        """
        sid = raw.get("sid")
        seq = raw.get("seq")
        if not isinstance(sid, int) or not isinstance(seq, int):
            return 0
        last = self._last_seq.get(sid)
        self._last_seq[sid] = seq
        if last is None or seq == last + 1:
            return 0
        gap = seq - last - 1
        if gap <= 0:
            # Out-of-order or duplicate; not a forward gap.
            return 0
        await self._emit(sid, last, seq, gap, raw)
        self.gaps_detected += 1
        return gap

    async def _emit(
        self,
        sid: int,
        last_seq: int,
        new_seq: int,
        gap_size: int,
        raw: dict[str, Any],
    ) -> None:
        msg = raw.get("msg") or {}
        market_ticker = msg.get("market_ticker") if isinstance(msg, dict) else None
        metadata = json.dumps(
            {
                "sid": sid,
                "last_seq": last_seq,
                "new_seq": new_seq,
                "channel_type": raw.get("type"),
                "market_ticker": market_ticker,
            }
        )
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO signals (event_ts, market_id, signal_type, value, metadata)
                VALUES ($1, NULL, 'gap_detected', $2, $3::jsonb)
                """,
                datetime.now(tz=UTC),
                float(gap_size),
                metadata,
            )
