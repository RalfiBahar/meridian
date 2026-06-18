# ROADMAP.md

8-phase plan from "scaffolded repo" to "production-grade quant terminal."
See `docs/roadmap.md` for the full narrative with resume bullets and concepts taught.

---

## Status summary

| Phase | Description | Status |
|---|---|---|
| 0 | Foundations: repo, Docker stack, CI, healthcheck | ✓ Done |
| 1a | Canonical event schema + SQL migrations | ✓ Done |
| 1b | Kalshi REST client + RSA-PSS auth | ✓ Done |
| 1c | Kalshi WebSocket ingestion worker | ~75% (1c.3 pending) |
| 1d | Polymarket ingestion + Prometheus/Grafana | ✓ Done |
| 2 | Implied probability + calibration engine | Planned |
| 3 | Cross-market no-arb consistency engine | Planned |
| 4 | Microstructure analytics + execution simulator | Planned |
| 5 | Implied Fed-rate distribution + event-response model | Planned |
| 6 | Research framework + portfolio optimizer | Planned |
| 7 | Quant Terminal frontend + production polish | Planned |

---

## Phase 0 — Foundations ✓

**Built**: Python 3.12 project with uv, ruff, mypy strict, pytest. Docker
Compose with TimescaleDB+Redis healthchecks. structlog JSON logging.
pydantic-settings config. asyncpg pool. async Redis client. healthcheck CLI.
GitHub Actions CI (3 parallel jobs).

**Deliverable**: `make health` reports all services healthy.

---

## Phase 1a — Canonical event schema + SQL migrations ✓

**Built**: `CanonicalEvent` pydantic v2 discriminated union over
`QuoteEvent`, `TradeEvent`, `BookEvent`, `StatusEvent`. SQL migration creating
OLTP tables + TimescaleDB hypertables. Forward-only migration runner with
SHA-256 checksums.

**Deliverable**: `make migrate` idempotent; `schema_migrations` tracks all versions.

---

## Phase 1b — Kalshi REST client + RSA-PSS auth ✓

**Built**: `KalshiSigner` (RSA-PSS request signing). `KalshiClient` (async
httpx wrapper). Typed Pydantic response models with Decimal prices.
`kalshi {status, markets, orderbook}` CLI commands. Live-verified against
production Fed funds futures data.

**Deliverable**: `meridian kalshi orderbook KXFED-26JUN-T3.75` prints real spread.

---

## Phase 1c — Kalshi WebSocket ingestion worker (~75%)

### 1c.1 ✓
`KalshiWebSocketClient` + `normalize_kalshi_message()`. Read-only WS tap.
`BookDeltaEvent` added to canonical event union. Deterministic UUIDv5 market IDs.

### 1c.2 ✓
`KalshiIngestWorker` + `TickWriter` + `MarketRegistry` + `GapDetector`.
Full persist pipeline with idempotent writes. Migration 0002 adds `book_delta`
kind, Kalshi-native `yes`/`no` book sides, NUMERIC size columns.

### 1c.3 — Pending
- Long-running ingest worker (no fixed `seconds` timeout)
- Reconnect with exponential backoff
- Redis Streams publishing (`XADD kalshi.events`)
- `meridian ingest kalshi --tickers ...` CLI command
- REST-driven backfill to enrich pending `markets` rows

**Deliverable (full 1c)**: `meridian ingest kalshi --tickers KXFED-26JUN-T3.75`
runs continuously, filling `ticks`, `book_snapshots`, `signals` from live data.

---

## Phase 1d — Polymarket ingestion + observability ✓

- Mirror of Kalshi worker for Polymarket CLOB API
- Same canonical schema, same idempotency model, same gap detection
- Prometheus metrics from both workers (lag histogram, reconnects, gaps, ticks/market)
- Provisioned Grafana dashboard

**Deliverable**: Both ingest workers running; Grafana shows live metrics across venues.

---

## Phase 2 — Implied probability + calibration engine

- `p_mid`, `p_bid`, `p_ask`, microprice, depth-weighted implied probability
- Calibration engine: Brier score + log loss over resolved markets
- Reliability diagrams per category stored as time-series
- Isotonic regression for systematic-bias correction
- CLI: `meridian analytics calibrate --category fed`

---

## Phase 3 — Cross-market no-arbitrage consistency engine

- `market_groups` populated for known partitions (e.g., all FOMC-date strikes)
- LP-based consistency checker: detect violations in [0,1] USD after fees
- Cross-venue checker for Kalshi ↔ Polymarket overlapping markets
- Live arb monitor with severity (bps) and depth-feasibility flag

---

## Phase 4 — Microstructure analytics + execution simulator

- Rolling: effective spread, realized spread, depth, OBI, microprice,
  Kyle's lambda, Amihud illiquidity
- Execution simulator: given a position + historical book replay, estimate
  fill price and slippage
- Per-market liquidity dashboard

---

## Phase 5 — Implied Fed-rate distribution + event-response model

- Implied PMF over future Fed funds rate from Kalshi FED contract strips
- Cross-validation against CME FedWatch
- Event-response analyzer: market reaction speed/magnitude on FOMC/CPI/BLS
  via Bayesian updating → "market efficiency latency" per category

---

## Phase 6 — Research framework + portfolio optimizer

- Reproducible experiment tracker: `experiments` table with code SHA, params,
  metrics, notes
- Per-experiment notebook pattern with `manifest.yaml`
- CLI: `meridian experiment run <name>`
- Portfolio optimizer: cvxpy Markowitz + Ledoit-Wolf shrinkage, walk-forward evaluation

---

## Phase 7 — Quant Terminal frontend + production polish

- Next.js + TypeScript frontend over FastAPI gateway
- Five panels: market scanner, deep view, arb monitor, calibration, Fed-rate
- WebSocket fan-out from FastAPI
- Rate limiting, auth, OpenTelemetry traces
- Public demo deployment at `meridian.example.com`

---

## Stretch goals (post-Phase-7)

- Regime detection: HMM over volatility states per category
- Anomaly detector: isolation forest / autoencoder over signal stream
- News → price NLP layer: fine-tuned tagger for market-moving events
- Simulated market maker: inventory management in the backtester
