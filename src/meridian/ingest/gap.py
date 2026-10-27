"""Sequence-gap detection on raw WS envelopes.

Per-``sid`` tracking: when ``seq`` jumps forward by more than 1, emit a row
in ``signals`` so later analysis can see the hole. The detector does not
backfill the missing messages and does not claim exactly-once delivery.

Cursor rules:

- The first accepted sequence for a sid becomes the baseline. There is no
  gap relative to messages that arrived before the process started.
- ``seq <= last`` is a duplicate or a reorder. It does not move the cursor
  backward, so the next forward sequence is not reported as a false gap.
- A forward gap is recorded, and the cursor moves to ``seq``, only inside
  ``commit``. Callers that persist the message must commit after that write
  succeeds. A failed write leaves the cursor where it was, so the next
  sequence is still measured against the last durable message.
- ``commit`` writes the gap row before moving the cursor. If that insert
  raises, the cursor stays put and a later message will report the hole
  again. Gap rows are therefore at-least-once. ``signals`` has no unique
  key, so a retry can insert a second ``gap_detected`` row.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import asyncpg

Action = Literal["ignore", "stale", "advance", "gap"]


@dataclass(frozen=True)
class SequenceObservation:
    """Classification of one envelope. Constructing this does not mutate state."""

    action: Action
    sid: int | None = None
    seq: int | None = None
    last_seq: int | None = None
    gap: int = 0


class GapDetector:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self._last_seq: dict[int, int] = {}
        self.gaps_detected = 0

    def classify(self, raw: dict[str, Any]) -> SequenceObservation:
        """Classify one envelope without moving the cursor or writing."""
        sid = raw.get("sid")
        seq = raw.get("seq")
        if not isinstance(sid, int) or not isinstance(seq, int):
            return SequenceObservation(action="ignore")
        last = self._last_seq.get(sid)
        if last is not None and seq <= last:
            return SequenceObservation(action="stale", sid=sid, seq=seq, last_seq=last)
        if last is None or seq == last + 1:
            return SequenceObservation(action="advance", sid=sid, seq=seq, last_seq=last)
        return SequenceObservation(
            action="gap",
            sid=sid,
            seq=seq,
            last_seq=last,
            gap=seq - last - 1,
        )

    async def commit(self, observation: SequenceObservation, raw: dict[str, Any]) -> int:
        """Apply a classification after the caller has finished its own write.

        Returns the forward gap size, or 0. Raises if the gap insert fails,
        and in that case leaves ``_last_seq`` unchanged.
        """
        if observation.action in ("ignore", "stale"):
            return 0
        if observation.sid is None or observation.seq is None:
            return 0
        if observation.action == "gap":
            if observation.last_seq is None or observation.gap <= 0:
                return 0
            await self._emit(
                observation.sid,
                observation.last_seq,
                observation.seq,
                observation.gap,
                raw,
            )
            self._last_seq[observation.sid] = observation.seq
            self.gaps_detected += 1
            return observation.gap
        self._last_seq[observation.sid] = observation.seq
        return 0

    async def observe(self, raw: dict[str, Any]) -> int:
        """Classify and commit immediately.

        The ingest worker does not use this. It classifies, persists, and
        commits only after the tick write succeeds. ``observe`` remains for
        callers that have no separate durable write.
        """
        return await self.commit(self.classify(raw), raw)

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
