# Roadmap

The 8-phase plan that takes Meridian from "scaffolded repo" to "production-
grade research platform with a quant-terminal frontend."

Each phase ends with three things:

1. A **deployable artifact** — something you can run and demo.
2. A **resume bullet** — a specific, defensible accomplishment.
3. **New math / stats / financial concepts** you can defend in an interview.

Cadence is aggressive daily (compressing ~15 weeks of work into ~6–8 weeks
of sessions). We pause before each phase to confirm scope and teach the
math.

---

## Phase 0 — Foundations ✓

**Built**: repo skeleton, pyproject with uv/ruff/mypy/pytest, Docker Compose
with TimescaleDB+Redis healthchecks, structlog JSON logging,
pydantic-settings config, asyncpg pool, async Redis client, healthcheck CLI,
GitHub Actions CI (lint+typecheck, unit tests, integration smoke).

**Artifact**: `make health` reports all services healthy, with versions.

**Resume bullet**: *Bootstrapped a fully-tooled Python 3.12 async service:
uv-managed dependencies, ruff (lint + format), mypy strict, pytest with
separated unit/integration markers, Docker Compose stack (TimescaleDB +
Redis with healthchecks), structlog JSON observability, pydantic-settings
config, asyncpg pool, async Redis client, and a CLI healthcheck that
verifies database extension state. GitHub Actions runs lint, typecheck,
unit tests, and a containerized integration smoke job on every push.*

**Concepts taught**: event-driven vs request-response for market data;
idempotency at the ingestion boundary; TimescaleDB rationale
(append-heavy time-series with frequent range scans, single DB for OLTP +
time-series, hypertables, continuous aggregates).

---

## Phase 1a — Canonical event schema + SQL migrations ✓

**Built**: `CanonicalEvent` Pydantic model wrapping a discriminated union
over `QuoteEvent`, `TradeEvent`, `BookEvent`, `StatusEvent`. Initial SQL
migration creating `venues`, `markets`, `market_groups`, `news_events`
(OLTP), and `ticks`, `book_snapshots`, `signals` (Timescale hypertables).
Forward-only migration runner with SHA-256 checksums.

**Artifact**: `make migrate` applies the schema, idempotent on re-run.
`schema_migrations` records every applied version.

**Resume bullet**: *Designed a cross-venue canonical event schema and an
idempotent forward-only SQL migration runner with checksum-based drift
detection. The schema supports L1 quotes, L2 depth snapshots, fills,
status transitions, and a generic signal stream that adds new analytics
without DDL changes.*

**Concepts taught**: discriminated unions for type-safe parsing;
`numeric(5,4)` vs floats for prices; UUIDs as venue-agnostic primary keys;
JSONB payload columns for venue-specific extras; hypertable partition
constraints.

---

## Phase 1b — Kalshi REST client + RSA-PSS auth ✓

**Built**: Typed async REST client (`KalshiClient`) over httpx with
context-managed lifecycle. `KalshiSigner` produces the three
`KALSHI-ACCESS-*` headers via RSA-PSS(SHA256). Env-routed URLs for demo
and production. CLI commands `kalshi {status, markets, orderbook}`.
Discovered the wire format quirks the hard way: `*_dollars` strings, not
integer cents; orderbook wrapped under `orderbook_fp`; status-filter
vocabulary differs from response enum.

**Artifact**: `meridian kalshi status`, `meridian kalshi markets`,
`meridian kalshi orderbook KXFED-26JUN-T3.75` all work against live
production with real Fed-rate data.

**Resume bullet**: *Built an async Kalshi REST client with RSA-PSS request
signing using a 2048-bit RSA keypair, env-routed for demo and production
environments. Typed Pydantic response models with Decimal-USD prices
preserve exact precision over thousands of intraday updates. Live-verified
against production by extracting implied probabilities from Fed funds
futures contracts.*

**Concepts taught**: RSA-PSS signature scheme; demo vs production
distinction in prediction-market sandboxes; order book mechanics on
binary contracts; deriving YES ask from NO bid via no-arbitrage; reading
implied probabilities from cross-strike Fed contracts.

---

## Phase 1c — Kalshi WebSocket ingestion worker — *in progress*

**Building**: Long-lived async worker subscribing to Kalshi WS channels
(`orderbook_delta`, `ticker`, `trade`, `market_lifecycle_v2`). Normalizes
payloads into `CanonicalEvent` (extending the union with `BookDeltaEvent`).
Writes to Timescale with `ON CONFLICT DO NOTHING` on `(market_id,
sequence_no, event_ts)`. Publishes events to Redis Streams. Reconnects
with exponential backoff. Per-stream sequence-gap detection emits
`signals.gap_detected` rows.

**Sub-phases**:

- 1c.1 — WS client + normalizer + `kalshi tap` CLI (read-only, no DB
  writes).
- 1c.2 — Persistence + idempotent writes + market discovery cache.
- 1c.3 — Long-running worker + reconnect/backoff + Redis Streams
  publishing.

**Artifact**: `meridian ingest kalshi --tickers ...` runs continuously,
filling `ticks`, `book_snapshots`, `signals` from live data.

**Resume bullet**: *Built a fault-tolerant Kalshi WebSocket ingestion
worker normalizing streaming order-book data into a canonical event
schema. Sequence-number gap detection with at-least-once delivery and
idempotent writes provides effectively-exactly-once semantics. Pushes
to Redis Streams for downstream consumers; reconnects with exponential
backoff.*

**Concepts taught**: WebSocket protocols and async iteration; sequence-
number gap detection; the at-least-once + idempotent-writes pattern;
in-memory L2 book maintenance via signed deltas; reconnect strategies.

---

## Phase 1d — Polymarket ingestion + observability — *planned*

**Building**: Mirror of the Kalshi worker for Polymarket's CLOB. Same
canonical schema, same idempotency model, same gap detection. Plus:
Prometheus metrics from both workers (ingestion lag histogram,
reconnects counter, gaps counter, ticks-written counter per market) and
a provisioned Grafana dashboard.

**Artifact**: `meridian ingest polymarket --tickers ...` running in
parallel; Grafana shows live ingestion metrics across both venues.

**Resume bullet**: *Extended the ingestion layer to Polymarket's CLOB API
using the same canonical event schema and idempotent write path, and
shipped a Prometheus + Grafana observability layer monitoring ingestion
lag, reconnect counts, and gap counts across both venues.*

**Concepts taught**: CLOB-vs-AMM hybrid markets; cross-venue ticker
mapping; Prometheus metrics conventions; latency histograms.

---

## Phase 2 — Implied probability + calibration engine — *planned*

**Building**: Analytics service computing `p_mid`, `p_bid`, `p_ask`,
microprice, and depth-weighted implied probability with confidence
intervals reflecting *liquidity* uncertainty (not just statistical noise).
Calibration engine that, for every resolved market, computes Brier and
log loss across the market's full history. Reliability diagrams per
category, stored as time-series so calibration drift over time is
visible. Isotonic regression for systematic-bias correction.

**Artifact**: live "implied probability + calibration" tab in the
upcoming terminal frontend (rendered in Phase 7); for now, a CLI
`meridian analytics calibrate --category fed` that prints reliability
metrics.

**Resume bullet**: *Implemented a calibration engine evaluating
prediction-market implied probabilities against realized outcomes via
Brier-score Murphy decomposition and isotonic recalibration, evaluated
over 10K+ resolved contracts.*

**Concepts taught**: Brier score and Murphy decomposition; log loss;
isotonic regression; reliability diagrams; microprice derivation.

---

## Phase 3 — Cross-market no-arb consistency engine — *planned*

**Building**: `market_groups` populated for known partitions (e.g. all
Kalshi Fed-strike markets for one FOMC meeting). An LP-based consistency
checker: given prices on a partition, solve `min/max p_i` subject to the
no-arb partition constraint plus bid/ask spread and fees. Flag
violations. Cross-venue checker for Kalshi↔Polymarket overlapping
markets.

**Artifact**: live arb monitor surfacing detected violations with
severity (in basis points after fees) and depth-feasibility flag.

**Resume bullet**: *Implemented a linear-programming-based no-arbitrage
consistency engine detecting price violations across related Kalshi /
Polymarket contracts, with depth-and-fee-aware feasibility scoring.*

**Concepts taught**: fundamental theorem of asset pricing (binary version);
LP modeling of partition + implication constraints; depth-feasibility as
the gating factor between apparent and real arbitrage.

---

## Phase 4 — Microstructure analytics + execution simulator — *planned*

**Building**: Rolling computation of effective spread, realized spread,
depth, order-book imbalance, microprice, Kyle's lambda (price impact per
unit signed volume), Amihud illiquidity. An execution simulator that,
given a desired position and a historical book replay, estimates fill
price and slippage.

**Artifact**: a "microstructure" CLI/dashboard showing per-market
liquidity metrics over time, plus a backtester for arbitrary execution
strategies.

**Resume bullet**: *Built a market-microstructure analytics layer
computing depth-weighted spreads, Kyle's lambda, and a historical
book-replay execution simulator with realistic slippage modeling.*

**Concepts taught**: Glosten-Milgrom adverse-selection theory of spreads;
Kyle's lambda as price-vs-signed-volume slope; effective vs realized
spread; PIN (probability of informed trading) — discussed, not
implemented.

---

## Phase 5 — Implied Fed-rate distribution + event-response model — *planned*

**Building**: Consume Kalshi's FED contracts across multiple FOMC dates.
Construct the implied PMF over future Fed funds rates at each meeting.
Cross-validate against CME FedWatch. An event-response analyzer that, for
tagged news events (FOMC, CPI, BLS), measures market reaction speed and
magnitude via Bayesian updating, producing a per-category "market
efficiency latency" signal.

**Artifact**: live Fed-rate panel showing the implied PMF for the next
3 FOMC meetings, side-by-side with FedWatch.

**Resume bullet**: *Derived implied probability distributions over future
Federal Reserve rate decisions from Kalshi contract prices,
cross-validated against CME FedWatch; quantified per-category market
efficiency via Bayesian event-response latency analysis.*

**Concepts taught**: risk-neutral vs physical probabilities; deriving an
implied PMF from a sorted strip of cumulative-survival contracts;
Bayesian updating with Beta priors for binary outcomes; strong / semi-
strong / weak market efficiency in the context of measured reaction
latencies.

---

## Phase 6 — Research framework + portfolio optimizer — *planned*

**Building**: Reproducible experiment tracker: every backtest writes a
row in `experiments` with `code_sha`, `params`, `data_window`, `metrics`,
`notes`. Per-experiment notebook pattern: a directory with `manifest.yaml`,
`run.py`, results notebook. CLI: `meridian experiment run <name>`.
Portfolio optimizer using `cvxpy` solving Markowitz with Ledoit-Wolf
shrinkage, walk-forward evaluation (never naive train/test).

**Artifact**: a research-grade backtester with full provenance for every
run, plus a CLI to manage experiment runs.

**Resume bullet**: *Designed a reproducible research framework with
provenance-tracked experiment runs, walk-forward backtesting, and
Ledoit-Wolf-shrunk mean-variance portfolio optimization over detected
market edges.*

**Concepts taught**: Markowitz mean-variance; Ledoit-Wolf shrinkage;
walk-forward analysis; survivorship and look-ahead bias; Kelly criterion
(fractional Kelly in practice).

---

## Phase 7 — Quant Terminal frontend + production polish — *planned*

**Building**: Bloomberg-lite Next.js + TypeScript frontend over a FastAPI
gateway. Five panels: market scanner, single-market deep view (price
chart, depth, ticks, news markers), arb monitor (live feed of violations),
calibration dashboard, Fed-rate panel. Plus: rate limiting, auth,
OpenTelemetry traces, public demo deployment.

**Artifact**: a live `meridian.example.com` deployment showing real-time
implied probabilities and analytics from production Kalshi (and
Polymarket) data.

**Resume bullet**: *Built a real-time "quant terminal" frontend (Next.js
+ WebSocket) visualizing implied probability curves, calibration drift,
arbitrage opportunities, and Fed-rate distributions over a streaming
backend pipeline.*

**Concepts taught**: TradingView-style chart libraries (lightweight-charts);
WebSocket fan-out from FastAPI; production-style observability with
OpenTelemetry; rate limiting and auth at the gateway.

---

## Beyond Phase 7 — stretch goals

Optional advanced features, each adding meaningful resume signal:

- **Regime detection**: HMM over volatility states per category.
- **Anomaly detector**: unsupervised (isolation forest / autoencoder)
  over the signal stream.
- **News → price NLP layer**: small fine-tune tagging news events that
  systematically move markets.
- **Simulated market maker**: not running live — shown in the backtester
  to demonstrate inventory management and adverse selection.

---

## How phases compound

Each phase isn't independent — they layer:

- Phase 1c's `signals` table is what Phase 2's calibration writes into.
- Phase 1d's Polymarket data is what Phase 3's cross-venue arb reads
  from.
- Phase 4's microstructure metrics are inputs to Phase 6's portfolio
  optimization.
- Phase 5's Fed implied PMF is rendered in Phase 7's terminal.

This compounding is the architectural payoff: by Phase 7 the system has
real depth, not just five disconnected demos.

---

## What "done" looks like

A live, public-demo `meridian.example.com` showing:

- Live order books for ~50 high-volume Kalshi markets, updated in real time.
- A calibration history for the Fed-rate series ("how well calibrated has
  this market been over the last 6 months?").
- An arb monitor flagging any current no-arb violations across related
  markets.
- An implied Fed-rate PMF for the next 3 FOMC meetings, with FedWatch
  comparison.
- A research notebook section showing 1-2 hypotheses tested via
  walk-forward backtest with full provenance.

That's the version you put on a resume.
