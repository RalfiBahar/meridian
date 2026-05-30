"""Ingestion layer: write CanonicalEvents to TimescaleDB.

Public surface:

- `MarketRegistry`  — lazy UPSERT-and-cache of `(venue, external_id) -> UUID`
- `TickWriter`      — routes CanonicalEvent payloads to ticks/book_snapshots
- `GapDetector`     — observes raw WS envelopes; emits `signals.gap_detected`
- `IngestStats`     — counters returned by a worker run
- `KalshiIngestWorker` — orchestrates WS -> normalize -> persist
"""

from __future__ import annotations

from meridian.ingest.gap import GapDetector
from meridian.ingest.registry import MarketRegistry
from meridian.ingest.stats import IngestStats
from meridian.ingest.worker import KalshiIngestWorker
from meridian.ingest.writer import TickWriter

__all__ = [
    "GapDetector",
    "IngestStats",
    "KalshiIngestWorker",
    "MarketRegistry",
    "TickWriter",
]
