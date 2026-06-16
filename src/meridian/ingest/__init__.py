"""Ingestion layer: write CanonicalEvents to TimescaleDB.

Public surface:

- `MarketRegistry`  — lazy UPSERT-and-cache of `(venue, external_id) -> UUID`
- `TickWriter`      — routes CanonicalEvent payloads to ticks/book_snapshots
- `GapDetector`     — observes raw Kalshi WS envelopes; emits `signals.gap_detected`
- `IngestStats`     — counters returned by a worker run
- `run_with_reconnect` — shared reconnect-with-backoff loop (see `reconnect.py`)
- `KalshiIngestWorker`     — orchestrates Kalshi WS -> normalize -> persist
- `PolymarketIngestWorker` — orchestrates Polymarket WS -> normalize -> persist
"""

from __future__ import annotations

from meridian.ingest.gap import GapDetector
from meridian.ingest.polymarket_worker import PolymarketIngestWorker
from meridian.ingest.reconnect import run_with_reconnect
from meridian.ingest.registry import MarketRegistry
from meridian.ingest.stats import IngestStats
from meridian.ingest.worker import KalshiIngestWorker
from meridian.ingest.writer import TickWriter

__all__ = [
    "GapDetector",
    "IngestStats",
    "KalshiIngestWorker",
    "MarketRegistry",
    "PolymarketIngestWorker",
    "TickWriter",
    "run_with_reconnect",
]
