# TASKS.md — Prioritized Backlog

Tasks are ordered by priority within each phase. "Ready" means all
dependencies are complete and the task can be started immediately.

Status legend: `[ ]` todo · `[~]` in progress · `[x]` done

---

## Phase 1c.3 — Long-running worker + Redis Streams (DONE)

These complete the Phase 1c deliverable.

- [x] **1c.3-a** Refactor `KalshiIngestWorker.run()` to run indefinitely
  (remove the `seconds` parameter / deadline loop; accept a stop event or
  signal instead)
- [x] **1c.3-b** Add reconnect with exponential backoff (initial 1s, max 60s,
  jitter ±20%) when the WS connection drops or raises an exception
- [x] **1c.3-c** Publish each persisted `CanonicalEvent` to a Redis Stream
  (`XADD kalshi.events *` with JSON-serialized event); use the existing
  `meridian.bus.redis` client
- [x] **1c.3-d** Add `meridian ingest kalshi --tickers T1,T2,...` CLI command
  (in `cli/ingest.py`) that boots the worker and runs until interrupted
- [x] **1c.3-e** REST-driven market enrichment: after inserting a
  `(pending REST sync)` market row, schedule a background REST call via
  `KalshiClient.get_market(ticker)` to fill in `question`, `category`,
  `opens_at`, `closes_at`, etc.
- [x] **1c.3-f** Add integration test covering reconnect: mock WS server that
  closes after N messages, assert worker reconnects and resumes counting
- [x] **1c.3-g** Update `CHANGELOG.md` with Phase 1c.3 changes

---

## Phase 1d — Polymarket ingestion + observability (READY)

- [x] **1d-a** Research Polymarket CLOB WebSocket API; document protocol quirks
  in `docs/polymarket.md`
- [x] **1d-b** Implement `src/meridian/polymarket/` mirror of `kalshi/`:
  `client.py`, `ws.py`, `normalize.py`, `models.py`, `endpoints.py`, `errors.py`
  (no `auth.py` — read-only CLOB market data needs no authentication, see
  `docs/polymarket.md`)
- [x] **1d-c** Add `Venue.POLYMARKET` normalization to `CanonicalEvent`;
  ensure same idempotency model (market identity keyed on `token_id`, see
  ADR-015; `MarketRegistry`/`TickWriter` already venue-agnostic, no schema
  changes needed)
- [x] **1d-d** Implement `PolymarketIngestWorker` following `KalshiIngestWorker` pattern
  (no `GapDetector` — Polymarket's market channel has no sequence number,
  see `docs/polymarket.md`; built on new shared `ingest/reconnect.py`)
- [x] **1d-e** Add ticker mapping table (`cross_market_links`) or use
  `market_groups` with `group_type='cross_venue'` for overlapping contracts
  — used the existing `market_groups`/`markets.market_group_id` (no schema
  change needed); added `meridian markets link-cross-venue <id_a> <id_b>
  --label ...` (`cli/markets.py`) to make it usable from the CLI
- [x] **1d-f** Add Prometheus metrics to both workers:
  - `ingest_events_total{venue, kind}` counter
  - `ingest_lag_seconds{venue}` histogram (now() - event_ts)
  - `ingest_reconnects_total{venue}` counter
  - `ingest_gaps_total{venue}` counter
- [x] **1d-g** Add `prometheus_client` dependency and `/metrics` HTTP endpoint
  (minimal `aiohttp` or `http.server` in a background thread)
- [x] **1d-h** Add Docker Compose service for `grafana:latest` with a
  provisioned dashboard JSON
- [x] **1d-i** Add Docker Compose service for `prom/prometheus` with scrape config
- [x] **1d-j** Write integration test for Polymarket normalizer (similar to
  `test_kalshi_normalize.py`) — plus `test_polymarket_client.py` and
  `test_polymarket_worker.py` (reconnect/backoff/redis-publish/enrichment,
  mirroring `test_ingest_worker.py`)

---

## Phase 2 — Implied probability + calibration engine (DONE)

- [x] **2-a** Implement `p_mid`, `p_bid`, `p_ask` extractor from `ticks` table
  (read latest quote row per market)
- [x] **2-b** Implement microprice: `(bid * ask_size + ask * bid_size) / (bid_size + ask_size)`
- [x] **2-c** Implement depth-weighted implied probability with confidence
  interval scaled by total resting liquidity
- [x] **2-d** Write computed signals to `signals` table
  (`signal_type` = `p_mid`, `microprice`, etc.)
- [x] **2-e** Calibration engine: for each resolved market, join `ticks`
  history to final `settled_value`; compute Brier score and log loss at
  each time point
- [x] **2-f** Reliability diagram computation: bin [0,1] into 10 buckets,
  compute mean predicted vs mean realized per bucket, store as `signals` rows
- [x] **2-g** Isotonic regression recalibration (using `scikit-learn` or
  custom implementation)
- [x] **2-h** CLI: `meridian analytics calibrate --category fed [--lookback 90d]`
- [x] **2-i** Add `scipy`, `numpy`, `scikit-learn` to `pyproject.toml` dependencies

---

## Phase 3 — No-arbitrage consistency engine (DONE)

- [x] **3-a** Populate `market_groups` for known Kalshi Fed-rate partitions
  (one group per FOMC meeting date, containing all strike contracts)
  — implemented as `meridian arb group-fed [--dry-run]` CLI command
- [x] **3-b** Implement LP-based partition checker using `scipy.optimize.linprog`
  or `cvxpy`: given observed bid/ask per contract in a partition, find if any
  price vector is consistent with no-arb (probabilities sum to 1 ± spread)
- [x] **3-c** Flag violations with severity in basis points (after standard
  Kalshi fee assumptions)
- [x] **3-d** Depth-feasibility gate: only flag as real arb if the violation
  exceeds spread + fee AND there is sufficient resting size to execute
- [x] **3-e** Cross-venue checker: for Kalshi ↔ Polymarket markets in
  `market_groups(type='cross_venue')`, detect price divergence
- [x] **3-f** Write violations to `signals` table
  (`signal_type='arb_violation_bps'`, `metadata` carries market IDs and details)
- [x] **3-g** CLI: `meridian arb monitor [--live] [--threshold-bps 5]`
- [x] **3-h** Add `cvxpy` to `pyproject.toml`

---

## Phase 4 — Microstructure analytics (DONE)

- [x] **4-a** Rolling effective spread: `ask - bid` at top of book
- [x] **4-b** Rolling order-book imbalance (OBI): `(bid_size - ask_size) / (bid_size + ask_size)`
- [x] **4-c** Kyle's lambda: OLS of price change on signed volume (rolling window)
- [x] **4-d** Amihud illiquidity ratio: `|return| / volume` per period
- [x] **4-e** Execution simulator: given a target position and a historical
  book snapshot series, compute expected fill price and slippage estimate
- [x] **4-f** Write computed metrics to `signals` table
- [x] **4-g** CLI: `meridian analytics microstructure <ticker> [--window 7d]`

---

## Phase 5 — Fed-rate distribution (READY)

- [ ] **5-a** Identify and group all active Kalshi FED contracts per FOMC date
  into `market_groups`
- [ ] **5-b** Construct implied PMF over rate outcomes from sorted cumulative
  contract strip (probability of rate ≤ k from strike ordering)
- [ ] **5-c** Fetch CME FedWatch probabilities (public CSV or scrape) for
  cross-validation
- [ ] **5-d** Event-response analyzer: for each `news_events` row (FOMC, CPI),
  measure delta in `p_mid` over pre/post windows; compute per-category
  "market efficiency latency" signal
- [ ] **5-e** CLI: `meridian analytics fedwatch [--date 2026-07-30]`

---

## Phase 6 — Research framework (BLOCKED on Phase 5)

- [ ] **6-a** Add `experiments` OLTP table (migration): `id`, `name`, `code_sha`,
  `params JSONB`, `data_window`, `metrics JSONB`, `notes`, `created_at`
- [ ] **6-b** CLI: `meridian experiment run <name>` — discovers `experiments/<name>/run.py`,
  captures output, writes row to `experiments`
- [ ] **6-c** Portfolio optimizer: Markowitz mean-variance with Ledoit-Wolf shrinkage
  using `cvxpy` + `numpy`
- [ ] **6-d** Walk-forward evaluation harness (no look-ahead)
- [ ] **6-e** Add `cvxpy` if not already present; add `pandas` for experiment analysis
- [ ] **6-f** Write 1–2 example experiments with full provenance

---

## Phase 7 — Quant Terminal (BLOCKED on Phase 6)

- [ ] **7-a** FastAPI app skeleton: `src/meridian/api/`; WebSocket + REST routes
- [ ] **7-b** Next.js + TypeScript frontend scaffold
- [ ] **7-c** Market scanner panel: live table of top markets by volume/OI
- [ ] **7-d** Single-market deep view: price chart, depth heatmap, ticks feed
- [ ] **7-e** Arb monitor panel: live feed of `arb_violation_bps` signals
- [ ] **7-f** Calibration dashboard: reliability diagrams per category
- [ ] **7-g** Fed-rate panel: implied PMF for next 3 FOMC meetings + FedWatch
- [ ] **7-h** Rate limiting + auth (API key or JWT)
- [ ] **7-i** OpenTelemetry traces
- [ ] **7-j** Public demo deployment (Fly.io or Railway)
- [ ] **7-k** Add `fastapi`, `uvicorn[standard]`, `opentelemetry-*` to `pyproject.toml`

---

## Ongoing / cross-cutting

- [ ] Increase test coverage for `ingest/writer.py` (currently untested in
  unit layer — requires a test DB fixture or a mock pool)
- [ ] Add `conftest.py` fixture for a mock `asyncpg.Pool` to enable unit
  testing of `TickWriter`, `MarketRegistry`, `GapDetector`
- [ ] Add `pytest-cov` minimum-coverage gate (e.g., 80%) to CI
- [ ] Add `make docs` target that lints docs with `markdownlint` or `vale`
- [ ] Refactor `KalshiIngestWorker.run()` to use the shared
  `ingest/reconnect.py:run_with_reconnect()` helper extracted during Phase
  1d (currently only `PolymarketIngestWorker` uses it; left Kalshi's
  tested 1c.3 reconnect loop untouched to avoid regression risk in this
  pass — the logic is otherwise identical)
- [ ] Document Polymarket auth mechanism in `docs/polymarket.md` once researched
