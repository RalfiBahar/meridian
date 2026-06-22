# ROADMAP.md

8-phase plan from "scaffolded repo" to "production-grade quant terminal."
See `docs/roadmap.md` for the full narrative with outcomes and concepts taught.

---

## Status summary

| Phase | Description | Status |
|---|---|---|
| 0 | Foundations: repo, Docker stack, CI, healthcheck | ✓ Done |
| 1a | Canonical event schema + SQL migrations | ✓ Done |
| 1b | Kalshi REST client + RSA-PSS auth | ✓ Done |
| 1c | Kalshi WebSocket ingestion worker | ✓ Done |
| 1d | Polymarket ingestion + Prometheus/Grafana | ✓ Done |
| 2 | Implied probability + calibration engine | ✓ Done |
| 3 | Cross-market no-arb consistency engine | ✓ Done |
| 4 | Microstructure analytics + execution simulator | ✓ Done |
| 5 | Implied Fed-rate distribution + event-response model | ✓ Done |
| 6 | Research framework + portfolio optimizer | ✓ Done |
| 7 | Quant Terminal frontend + production polish | ✓ Done |

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

## Phase 1c — Kalshi WebSocket ingestion worker ✓

### 1c.1 ✓
`KalshiWebSocketClient` + `normalize_kalshi_message()`. Read-only WS tap.
`BookDeltaEvent` added to canonical event union. Deterministic UUIDv5 market IDs.

### 1c.2 ✓
`KalshiIngestWorker` + `TickWriter` + `MarketRegistry` + `GapDetector`.
Full persist pipeline with idempotent writes. Migration 0002 adds `book_delta`
kind, Kalshi-native `yes`/`no` book sides, NUMERIC size columns.

### 1c.3 ✓
- Long-running ingest worker (no fixed `seconds` timeout)
- Reconnect with exponential backoff
- Redis Streams publishing (`XADD kalshi.events`)
- `meridian ingest kalshi --tickers ...` CLI command
- REST-driven backfill to enrich pending `markets` rows

**Deliverable**: `meridian ingest kalshi --tickers KXFED-26JUN-T3.75`
runs continuously, filling `ticks`, `book_snapshots`, `signals` from live data.

---

## Phase 1d — Polymarket ingestion + observability ✓

- Mirror of Kalshi worker for Polymarket CLOB API
- Same canonical schema, same idempotency model, same gap detection
- Prometheus metrics from both workers (lag histogram, reconnects, gaps, ticks/market)
- Provisioned Grafana dashboard

**Deliverable**: Both ingest workers running; Grafana shows live metrics across venues.

---

## Phase 2 — Implied probability + calibration engine ✓

- `p_mid`, `p_bid`, `p_ask`, microprice, depth-weighted implied probability
- Calibration engine: Brier score + log loss over resolved markets
- Reliability diagrams per category stored as time-series
- Isotonic regression for systematic-bias correction
- CLI: `meridian analytics calibrate --category fed`

---

## Phase 3 — Cross-market no-arbitrage consistency engine ✓

- `market_groups` populated for known partitions (e.g., all FOMC-date strikes)
- LP-based consistency checker: detect violations in [0,1] USD after fees
- Cross-venue checker for Kalshi ↔ Polymarket overlapping markets
- Live arb monitor with severity (bps) and depth-feasibility flag

---

## Phase 4 — Microstructure analytics + execution simulator ✓

**Built**: `analytics/microstructure.py` — effective spread (mean ask−bid),
OBI `(bid_size−ask_size)/(bid_size+ask_size)`, Kyle's λ (OLS ΔP∼signed_volume),
Amihud illiquidity ratio (|return|/volume). Execution simulator walks live book
levels (ask-side ascending for buy, bid-side descending for sell) computing avg
fill price and slippage. All four metrics written to `signals` table.
`cli/analytics.py` extended with `meridian analytics microstructure <ticker>
[--window DAYS] [--simulate-buy QTY] [--simulate-sell QTY] [--write-signals]`.
19 unit tests.

**Deliverable**: `meridian analytics microstructure KXFED-26JUN-T3.75 --window 7`
prints effective spread, OBI, Kyle's lambda, Amihud. `--simulate-buy 100`
estimates fill for a 100-contract buy.

---

## Phase 5 — Implied Fed-rate distribution + event-response model ✓

**Built**: `analytics/fedwatch.py` — `build_kalshi_pmf()` reads the latest
`p_mid` signals for every KXFED contract in a partition group, sorts by strike
rate parsed from the ticker, normalizes into a proper PMF, and exposes
`expected_rate()` and `entropy()` (Shannon bits).  `fetch_cme_fedwatch()`
makes a best-effort HTTP call to the CME 30-day Fed Funds futures endpoint and
derives a 2-strike discrete PMF from the implied rate; fails silently on any
network/format error.  `compute_event_response()` joins `news_events` to
per-market `p_mid` signals over configurable pre/post windows, returning mean
ΔP_mid and a variance ratio (post/pre > 1 = information arrival).  CLI:
`meridian analytics fedwatch [--date YYYY-MM-DD] [--cme]` and
`meridian analytics event-response <uuid> [--pre MIN] [--post MIN]`.
25 unit tests.

**Deliverable**: `meridian analytics fedwatch --date 2026-07-30 --cme` prints
side-by-side Kalshi vs CME FedWatch PMFs for the July FOMC meeting.

---

## Phase 6 — Research framework + portfolio optimizer ✓

**Built**: `migrations/0003_experiments.sql` — `experiments` table (id, name,
code_sha, params, data_window, metrics, stdout, status, started_at, completed_at).
`research/experiment.py` discovers `experiments/<name>/run.py`, SHA-256s it for
provenance, executes in a subprocess with `EXPERIMENT_PARAMS` env var, captures
stdout, extracts last-line JSON as `metrics`, persists to DB.
`research/portfolio.py` — Markowitz min-var QP with Ledoit-Wolf OAS shrinkage
(`sklearn.covariance.OAS`) solved by CLARABEL via `cvxpy`; `efficient_frontier()`
sweeps N portfolios; all weights normalized post-solve.
`research/walkforward.py` — expanding/rolling fold generator; `evaluate()` runs
any optimizer function on each fold's train data and scores on OOS period (Sharpe,
max drawdown, ann. return/vol).
Two example experiments: `kalshi_fed_pmf` (prints KXFED PMF + metrics JSON) and
`arb_snapshot` (daily arb violation snapshot + metrics JSON).
30 unit tests.

**Deliverable**: `meridian experiment run kalshi_fed_pmf` prints the KXFED PMF
and writes a provenance row to the `experiments` table.
`meridian experiment portfolio --category fed --walk-forward` prints walk-forward
Sharpe/drawdown across expanding windows of Fed-rate market probabilities.

---

## Phase 7 — Quant Terminal frontend + production polish

- Next.js + TypeScript frontend over FastAPI gateway
- Five panels: market scanner, deep view, arb monitor, calibration, Fed-rate
- WebSocket fan-out from FastAPI
- Rate limiting, auth, OpenTelemetry traces
- Public demo deployment at `meridian.example.com`

---

## Stretch goals (post-Phase-7) — all complete

- [x] Regime detection: HMM over volatility states per category (Phase 8)
- [x] Anomaly detector: isolation forest over signal stream (Phase 8)
- [x] Simulated market maker: inventory management in the backtester (Phase 9)
- [x] News → price NLP layer: TF-IDF + LogReg market-moving event tagger (Phase 10)
