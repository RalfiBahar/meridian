"""Prometheus metrics for the ingestion workers.

Both `KalshiIngestWorker` and `PolymarketIngestWorker` import these
module-level metric objects directly and label every observation with
`venue` (`"kalshi"` / `"polymarket"`) so one Grafana dashboard can compare
both. `start_metrics_server()` wraps `prometheus_client.start_http_server`,
which runs its own threaded HTTP server — no need for a separate aiohttp
app for a single `/metrics` endpoint (see ADR-017 in DECISIONS.md).
"""

from __future__ import annotations

from prometheus_client import Counter, Histogram, start_http_server

ingest_events_total = Counter(
    "ingest_events_total",
    "CanonicalEvents persisted, by venue and payload kind.",
    ["venue", "kind"],
)

ingest_lag_seconds = Histogram(
    "ingest_lag_seconds",
    "Wall-clock seconds between an event's venue timestamp and our ingest time.",
    ["venue"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
)

ingest_reconnects_total = Counter(
    "ingest_reconnects_total",
    "WebSocket reconnects, by venue.",
    ["venue"],
)

ingest_gaps_total = Counter(
    "ingest_gaps_total",
    "Detected sequence-number gaps, by venue. Always 0 for venues with no "
    "wire-level sequence number (see docs/polymarket.md).",
    ["venue"],
)


def start_metrics_server(port: int) -> None:
    """Start the `/metrics` HTTP server in a background thread."""
    start_http_server(port)
