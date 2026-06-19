# CHANGELOG

All notable changes to Meridian. Newest first.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

*All phases and all four stretch goals complete.*

### Added
- `tests/test_ingest_reconnect.py` — 12 unit tests for the shared
  `run_with_reconnect` reconnect loop (`ingest/reconnect.py`), covering
  all previously-uncovered branches: `_drain_stream` ConnectionClosedOK
  suppression and exception propagation; `_run_connection` without
  stop_event, stop_event firing mid-drain, drain exception re-raise, and
  CancelledError propagation; `run_with_reconnect` CancelledError, pre-set
  stop_event (skips connect), ConnectionClosedOK + stop_event already set,
  ConnectionClosedOK triggering reconnect + `on_reconnect` callback, and
  CancelledError during `asyncio.wait`. `reconnect.py` reaches 100% coverage.

---

## Phase 10 — News → Price NLP Tagger — 2026-06-19

### Added
- `src/meridian/analytics/nlp.py` — TF-IDF + LogisticRegression news tagger:
  - `NewsTaggerConfig` dataclass: price_delta_threshold, prediction_threshold,
    max_features, ngram_range, post_window_hours, cv_folds
  - `NewsTagResult` dataclass: event_id, label, category, market_moving_prob,
    is_market_moving, top_tokens
  - `NewsTagger` dataclass: trained classifier + config + CV accuracy;
    `predict_one(label, category, event_id)` classifies a single event;
    `summary()` prints model configuration
  - `train_tagger(pool, *, category, window, config)` — fetches news events,
    measures abs(mean_delta_p) in the post_window_hours after each event for
    markets in the same category, builds (label, category) → binary labels,
    trains a TF-IDF (sublinear_tf, max_features, ngrams) + balanced
    LogisticRegression pipeline; returns None when < 10 labeled examples or
    when only one class is present in the training data
  - `tag_recent_events(pool, tagger, *, category, window)` — fetches recent
    news events and classifies each with the trained tagger
  - `write_nlp_signals(pool, results)` — persists market_moving_prob signals
    to the signals table (one row per result; value = predicted probability;
    metadata carries label, category, is_market_moving flag, top_tokens)
  - `_event_text(label, category)` — combines category prefix + label into
    a single string for TF-IDF input
  - `_top_tokens_for_text(pipeline, text)` — returns the top-5 TF-IDF tokens
    in the text that have the highest positive LogisticRegression coefficient
    (most predictive of market-moving class)
- `src/meridian/cli/analytics.py`:
  - `meridian analytics nlp-tag [--category CATEGORY] [--train-window DAYS]
    [--tag-window DAYS] [--price-threshold FLOAT] [--write-signals]`
    — trains tagger, prints summary, classifies recent events sorted by
    market_moving_prob
- `tests/test_analytics_nlp.py` — 18 unit tests:
  - `_event_text`: combines category + label, category differentiation
  - `train_tagger`: no events → None, < min events → None, sufficient data →
    tagger, categories recorded, custom config, moving vs stable separation
  - `predict_one`: result fields, threshold=0 → always True, top_tokens list
  - `_top_tokens_for_text`: returns strings, handles OOV text
  - `tag_recent_events`: returns list with labels, empty on no events
  - `write_nlp_signals`: executemany called, empty list → no call
  - `NewsTagger.summary()` format

### Changed
- `TASKS.md` — Phase 10 tasks added and marked `[x]`
- `AGENTS.md` — Phase 10 status added; nlp.py added to source layout

---

## Phase 9 — Simulated Market Maker — 2026-06-19

### Added
- `src/meridian/research/marketmaker.py` — event-driven market-making backtest:
  - `MarketMakerConfig` dataclass: half_spread, base_size, max_inventory,
    skew_threshold (all Decimal); symmetric quoting, inventory skew, suppression
  - `Fill` dataclass: ts, side, price, size — one record per simulated fill
  - `MarketMakerResult` dataclass: full backtest output including realized P&L,
    MTM P&L, total P&L, fill rate, Sharpe (annualised), per-tick P&L series,
    fill list, max inventory reached; `summary()` returns formatted report
  - `run_mm_backtest(pool, market_id, *, config, window)` — fetches quote and
    trade ticks from the DB; returns None when no quote ticks in window
  - `_simulate_mm(market_id, config, window, quotes, trades)` — pure
    event-driven simulation: merges and time-sorts quote + trade ticks; quote
    ticks update our posted bid/ask with inventory-adjusted sizing; trade ticks
    generate fills when they cross our posted price; FIFO realized P&L tracking
    via `_realize_pnl`; MTM P&L from open inventory at final midprice; daily
    P&L bucketing for Sharpe calculation
  - `_realize_pnl(open_longs, sell_price, sell_size)` — pure FIFO P&L function;
    closes sell_size worth of open long lots oldest-first; mutates lot list in place
  - `_fetch_ticks(pool, market_id, since)` — fetches quote ticks (bid, ask,
    bid_size, ask_size) and trade ticks (trade_price, trade_size, aggressor) from
    the `ticks` hypertable; returns (quotes, trades) tuple
- `src/meridian/cli/analytics.py`:
  - `meridian analytics marketmaker TICKER [--half-spread DECIMAL]
    [--base-size INT] [--max-inventory INT] [--window DAYS]` — runs backtest
    and prints `MarketMakerResult.summary()`
- `tests/test_research_marketmaker.py` — 17 unit tests:
  - `_realize_pnl`: simple round-trip, partial fill, FIFO across multiple lots,
    empty lots list
  - `_simulate_mm`: no trades (no fills), trade lifts ask (sell fill), trade
    hits bid (buy fill), midprice trade (no fill), round-trip P&L positive,
    fill rate calculation, inventory suppresses buy side after max reached,
    max_inventory_reached tracking, Sharpe None with single day of data
  - `run_mm_backtest`: None on no quotes, result with quotes, custom config
  - `MarketMakerResult.summary()` format

### Changed
- `TASKS.md` — Phase 9 tasks 9-a through 9-d marked `[x]`
- `AGENTS.md` — Phase 9 status added and marketmaker.py added to source layout

---

## Phase 8 — Anomaly Detection + Regime Detection — 2026-06-19

### Added
- `src/meridian/analytics/anomaly.py` — Isolation Forest anomaly detector:
  - `_build_feature_matrix(rows)` — pivots signal rows into hourly
    (market_id, hour) feature buckets; missing features encoded as NaN;
    all-NaN buckets dropped; latest value wins within each bucket
  - `detect_anomalies(pool, *, market_id, window, contamination)` — fits
    `IsolationForest` on microprice / effective_spread / obi / kyle_lambda /
    amihud signals; median-imputes missing features; StandardScaler before
    fitting; returns `AnomalyReport` or None when < 10 observations
  - `write_anomaly_signals(pool, report)` — persists `anomaly_score` rows
    (decision-function output; negative = anomalous) to `signals` table
  - `AnomalyPoint` dataclass: ts, market_id, features dict, score, is_anomaly
  - `AnomalyReport` dataclass: market_ids, window, contamination,
    n_observations, n_anomalies, points, `summary()` (top 10 anomalies)
- `src/meridian/analytics/regime.py` — Gaussian HMM regime detector:
  - `_build_sequences(rows)` — groups effective_spread + obi signals into
    per-market hourly time-ordered float64 sequences; NaN for missing features
  - `detect_regimes(pool, *, category, n_states, window)` — fits
    `GaussianHMM(n_components=n_states, covariance_type="diag")` via
    multi-sequence API; Viterbi decoding; states labeled low/medium/high by
    ascending mean effective_spread; returns `RegimeResult` or None when
    < 20 observations or HMM fails to converge
  - `write_regime_signals(pool, result)` — persists `regime_state` rows
    (value = state_id 0/1/2; metadata carries state_name + posterior prob)
  - `RegimePoint` dataclass: ts, market_id, state_id, state_name, prob
  - `RegimeResult` dataclass: category, n_states, n_observations,
    current_state, points, `summary()` (state frequencies table)
- `src/meridian/cli/analytics.py`:
  - `meridian analytics anomaly [--market TICKER] [--window DAYS]
    [--contamination FLOAT] [--write-signals]` — anomaly detection CLI
  - `meridian analytics regime [--category CATEGORY] [--n-states N]
    [--window DAYS] [--write-signals]` — regime detection CLI
- `tests/test_analytics_anomaly.py` — 17 unit tests: feature matrix (empty,
  single bucket, two markets, latest-value-wins, all-NaN drops,
  ignores-unknown types), detect_anomalies (no markets, too few obs,
  10 obs report, single-market arg, known outlier, contamination, fields),
  write_anomaly_signals (executemany, empty), summary format
- `tests/test_analytics_regime.py` — 19 unit tests: sequences (empty, single
  bucket, two markets, non-regime features, all-NaN drops, ordering),
  detect_regimes (no markets, too few obs, sufficient obs, state names,
  state ids, 2-state, posteriors valid, current state, category query),
  write_regime_signals (executemany, empty), summary format

### Changed
- `pyproject.toml` — added `hmmlearn>=0.3.0` dependency; added `hmmlearn.*`
  to mypy `ignore_missing_imports` overrides
- `TASKS.md` — Phase 8 tasks 8-a through 8-c marked `[x]`
- `AGENTS.md` — Phase 8 status updated to Done

---

## Cross-cutting — test coverage + CI gate + refactor — 2026-06-19

### Added
- `tests/conftest.py` — `MockConn` and `MockPool` classes + `mock_conn` /
  `mock_pool` pytest fixtures; shared asyncpg stand-ins that record every
  `execute` / `executemany` / `fetchrow` call so unit tests can assert on SQL
  without a real database
- `tests/test_ingest_writer.py` — 17 unit tests covering all 5 event-payload
  routes in `TickWriter.write()`: QuoteEvent (with/without sizes, ON CONFLICT
  0 vs 1), TradeEvent (aggressor present/null), BookEvent (empty levels → 0,
  non-empty → `executemany` + correct count), BookDeltaEvent (JSON payload
  structure), StatusEvent (JSON payload per status value), parametrized
  INSERT 0 1 / INSERT 0 0 idempotency
- `tests/test_ingest_registry.py` — 10 unit tests covering `MarketRegistry`:
  first `ensure_market` returns True + increments counter, second call hits
  cache (no extra DB round-trip), ON CONFLICT returns False without counting,
  conflict result still caches to avoid future DB hits, two distinct markets
  both inserted, venue SELECT issued once then cached, missing venue raises
  `RuntimeError`, venue code forwarded to query
- `tests/test_ingest_gap.py` — 15 unit tests covering `GapDetector.observe()`:
  first message no gap, sequential no gap, independent sids, forward gap
  returns size + emits signal row, gap of 1 detected, multiple gaps counted,
  duplicate seq returns 0, out-of-order returns 0, missing sid/seq/non-int
  sid/empty message all return 0, no DB call on no-gap sequences, signal
  metadata contains sid/last_seq/new_seq
- `pyproject.toml` — `[tool.coverage.run]` omit config for `src/meridian/cli/*`
  and `src/meridian/research/experiment.py` (Click wrappers and subprocess
  runner exercised only through integration tests)
- `Makefile` — `make docs` target: `npx markdownlint-cli "docs/**/*.md" "*.md"`

### Changed
- `src/meridian/ingest/worker.py` — `KalshiIngestWorker.run()` now delegates
  to `run_with_reconnect()` from `ingest/reconnect.py`, matching
  `PolymarketIngestWorker`; removed duplicate backoff constants
  (`_BACKOFF_INITIAL`, `_BACKOFF_MAX`, `_BACKOFF_JITTER`), `_wait_for_event`,
  `_run_connection`, and `_drain_stream`; added `_connect()` method;
  reconnect Prometheus counter wired via `on_reconnect` callback
- `tests/test_ingest_worker.py` — updated backoff monkeypatch targets from
  `meridian.ingest.worker.*` to `meridian.ingest.reconnect.*`
- `.github/workflows/ci.yml` — unit-tests job now runs
  `pytest --cov=src/meridian --cov-report=term-missing --cov-fail-under=80`
  (83% measured after CLI omit)
- `ROADMAP.md` — Phase 1c and Phase 7 status updated to ✓ Done
- `TASKS.md` — all cross-cutting ongoing items marked `[x]`

---

## Phase 7-j — Fly.io deployment config — 2026-06-18

### Added
- `Dockerfile` — multi-stage build for the FastAPI backend: installs uv,
  syncs deps layer (cached), copies `src/` + `README.md`, runs
  `uvicorn meridian.api.app:app --workers 2`; verified `docker build` succeeds
- `.dockerignore` — excludes tests, docs, frontend, and dev artefacts; keeps
  `README.md` (required by hatchling) and `src/`
- `fly.toml` — Fly.io config for `meridian-api`: shared CPU 512 MB, `ord`
  region, HTTPS, auto-stop, `GET /health` health check every 30 s
- `frontend/Dockerfile` — three-stage Next.js build (deps → builder → runner)
  using `output: "standalone"` for a minimal production image; `MERIDIAN_API_URL`
  configurable at build time via `--build-arg`
- `frontend/.dockerignore` — excludes `node_modules/`, `.next/`, env files
- `frontend/fly.toml` — Fly.io config for `meridian-frontend`: shared CPU
  512 MB, `ord` region, HTTPS, `GET /` health check
- `frontend/next.config.ts` — added `output: "standalone"` for Docker support

### Deploy guide (after `flyctl` install and `flyctl auth login`)
```sh
# Backend (requires Postgres + Redis — use Fly Postgres and Upstash Redis)
flyctl launch --config fly.toml --no-deploy
flyctl secrets set MERIDIAN_API_KEYS=<key> DATABASE_URL=<pg-url> REDIS_URL=<redis-url>
flyctl deploy --config fly.toml

# Frontend
cd frontend
flyctl launch --config fly.toml --no-deploy
flyctl secrets set MERIDIAN_API_URL=https://meridian-api.fly.dev \
                   NEXT_PUBLIC_WS_URL=wss://meridian-api.fly.dev \
                   NEXT_PUBLIC_API_KEY=<key>
flyctl deploy --config fly.toml
```

---

## Phase 7-c–g — Frontend panels — 2026-06-18

### Added / Changed (frontend)
- `src/components/MarketScannerClient.tsx` — `"use client"` component using
  `useWs("/ws/markets")` for live 5-second snapshots; connection status dot;
  `markets/page.tsx` now renders it with server-side initial data + status
  filter tab bar (open / closed / settled)
- `src/components/MarketDetailClient.tsx` — `"use client"` component using
  `useWs("/ws/markets/{id}")` for live tick feed; prepends incoming ticks to
  the front of the list (max 200 kept); live bid/ask header updated from each
  tick event; `markets/[id]/page.tsx` delegates the live section to it while
  keeping signals grid and book snapshot server-rendered
- `src/components/ArbMonitorClient.tsx` — `"use client"` component using
  `useWs("/ws/arb")` for 30-second arb snapshots; severity-color badges (red
  ≥50 bps, yellow ≥20 bps); `arb/page.tsx` passes server-fetched initial data
- `src/app/calibration/page.tsx` — enhanced: category tab bar (fed/econ/
  politics/crypto/sports), color-coded bar reliability chart with diagonal
  reference line, green/red metric tiles for Brier/log-loss vs thresholds
- `src/app/fedwatch/page.tsx` — shows all 3 upcoming FOMC meetings as cards
  (each with its own server fetch); overlay Kalshi + CME PMF bars per card;
  bottom strike table with bps-delta column; active-date URL param

---

## Phase 7-b — Frontend scaffold — 2026-06-18

### Added
- `frontend/` — Next.js 15 + TypeScript App Router project
  - `package.json` — deps: `next`, `react`, `react-dom`, TypeScript, ESLint
  - `tsconfig.json` — strict TypeScript; `@/*` path alias for `src/`
  - `next.config.ts` — rewrites `/api/*` → `MERIDIAN_API_URL` to proxy the
    FastAPI backend (eliminates CORS in dev; single origin in prod)
  - `.env.local.example` — documents `MERIDIAN_API_URL`, `NEXT_PUBLIC_WS_URL`,
    `NEXT_PUBLIC_API_KEY`
  - `src/types/api.ts` — TypeScript interfaces mirroring all Pydantic response
    models: `MarketSummary`, `MarketsResponse`, `MarketDetail`, `MarketSignals`,
    `TickRow`, `BookLevel`, `ArbViolationsResponse`, `CalibrationResponse`,
    `FedWatchResponse`, `FedPMF`, `WsEvent`
  - `src/lib/api.ts` — typed `apiFetch` helper; exports `fetchMarkets`,
    `fetchMarket`, `fetchArbViolations`, `fetchCalibration`, `fetchFedWatch`;
    server-side calls use `MERIDIAN_API_URL` directly; client-side calls use
    the proxy rewrite; `X-API-Key` header from `NEXT_PUBLIC_API_KEY`
  - `src/lib/ws.ts` — `useWs` React hook: opens WebSocket to
    `NEXT_PUBLIC_WS_URL`, parses JSON frames into typed `WsEvent`, reconnects
    on close with configurable delay
  - `src/app/globals.css` — dark terminal theme (CSS variables, monospace font,
    table/badge/panel utilities)
  - `src/app/layout.tsx` — root layout: sticky nav with links to all 5 panels
  - `src/app/page.tsx` — redirects `/` → `/markets`
  - `src/app/markets/page.tsx` — server-rendered market scanner: paginated
    table with bid/ask/mid/microprice/spread, linked tickers, pagination
  - `src/app/markets/[id]/page.tsx` — server-rendered market deep view:
    signals grid, L2 book (bid/ask columns), recent ticks table
  - `src/app/arb/page.tsx` — server-rendered arb monitor: partition violations
    and cross-venue divergences with severity badges
  - `src/app/calibration/page.tsx` — server-rendered calibration dashboard:
    Brier/log-loss metrics, bar reliability chart, bin-level table
  - `src/app/fedwatch/page.tsx` — server-rendered Fed-rate panel: Kalshi vs
    CME PMF bar charts, per-strike probability table with delta column

---

## Phase 7 (backend) — 2026-06-18

### Added
- `src/meridian/api/__init__.py` — package marker for Phase 7 FastAPI gateway
- `src/meridian/api/app.py` — `create_app()` factory: FastAPI with CORS,
  `RateLimitMiddleware` (sliding-window per-key/IP, 120 req/min default),
  REST + WebSocket routers; module-level `app` singleton for uvicorn
- `src/meridian/api/auth.py` — `require_api_key` dependency (401 missing /
  403 wrong); `get_valid_keys()` reads `MERIDIAN_API_KEYS` env var (empty =
  open access); WebSocket auth via `?api_key=` query param
- `src/meridian/api/deps.py` — `lifespan` context manager: opens asyncpg
  pool + Redis client + `EventHub`; `get_pool` / `get_redis` / `get_hub`
  FastAPI dependency functions
- `src/meridian/api/hub.py` — `EventHub`: reads from Redis Streams
  (`kalshi.events`, `polymarket.events`), fan-outs to asyncio Queues by
  channel (`"all"` or `"market:{uuid}"`); `subscribe` / `unsubscribe`;
  graceful cancel on `asyncio.CancelledError`; 1s back-off on Redis errors
- `src/meridian/api/models.py` — Pydantic v2 response schemas: `MarketSummary`,
  `MarketsResponse`, `MarketDetail`, `MarketSignalsModel`, `TickRow`,
  `BookLevel`, `ArbViolationsResponse`, `CalibrationResponse`, `FedWatchResponse`
- `src/meridian/api/routes/health.py` — `GET /health` (public)
- `src/meridian/api/routes/markets.py` — `GET /api/v1/markets` (paginated,
  LATERAL JOIN signals), `GET /api/v1/markets/{id}` (deep view),
  `WS /ws/markets` (5 s snapshots), `WS /ws/markets/{id}` (live EventHub)
- `src/meridian/api/routes/arb.py` — `GET /api/v1/arb/violations`,
  `WS /ws/arb` (30 s snapshots)
- `src/meridian/api/routes/calibration.py` — `GET /api/v1/calibration`
- `src/meridian/api/routes/fedwatch.py` — `GET /api/v1/fedwatch`
- `src/meridian/api/telemetry.py` — `configure_telemetry()` (OTLP gRPC
  exporter) + `instrument_fastapi(app)` via `FastAPIInstrumentor`
- `src/meridian/cli/serve.py` — `meridian serve [--host] [--port] [--workers]
  [--reload] [--metrics-port]` CLI command; starts Prometheus metrics server
  then hands off to uvicorn
- `tests/test_api.py` — 17 unit tests: health, market list/detail, arb/
  calibration/fedwatch (empty-pool early-exit paths), auth enforcement
  (401/403/200), rate limiting (3-req window, health exempt), EventHub
  subscribe/publish/unsubscribe/drop-full-queue

### Changed
- `pyproject.toml` — added `fastapi>=0.115.0`, `uvicorn[standard]>=0.34.0`,
  `opentelemetry-sdk>=1.28.0`, `opentelemetry-instrumentation-fastapi>=0.49b0`,
  `opentelemetry-exporter-otlp-proto-grpc>=1.28.0`; ruff `B008` per-file
  ignore for `src/meridian/api/routes/*.py`; mypy test override adds
  `disallow_untyped_calls = false`
- `src/meridian/cli/__main__.py` — registered `serve` command

---

## Phase 6 — 2026-06-18

### Added
- `migrations/0003_experiments.sql` — `experiments` OLTP table: `id`, `name`,
  `code_sha` (SHA-256 of `run.py`), `params JSONB`, `data_window JSONB`,
  `metrics JSONB`, `stdout TEXT`, `notes TEXT`, `status` (pending/running/
  completed/failed), `started_at`, `completed_at`, `created_at`; indexed on
  `(name, created_at DESC)` and `status`
- `src/meridian/research/__init__.py` — package marker
- `src/meridian/research/experiment.py`:
  - `_experiments_root()` — resolves `experiments/` dir by walking up to repo root
  - `discover_experiments()` — lists all `experiments/<name>/run.py` scripts
  - `_load_manifest(exp_dir)` — reads `manifest.yaml` params defaults (PyYAML
    optional; fails silently if not installed)
  - `_extract_metrics(stdout)` — parses last-line JSON from experiment output
  - `run_experiment(pool, name, *, params, data_window, notes, run_timeout)` —
    SHA-256s run.py for provenance, inserts `pending` row, spawns subprocess
    with `EXPERIMENT_PARAMS` env var, captures stdout, updates to
    `completed`/`failed`, extracts metrics JSON from last line
  - `list_experiments(pool, *, name, limit)` — queries recent runs from DB
  - `ExperimentResult` dataclass with `.success` property and `.summary()`
- `src/meridian/research/portfolio.py`:
  - `ledoit_wolf_shrinkage(returns)` — OAS estimator via `sklearn.covariance.OAS`
  - `sample_covariance(returns)` — plain sample covariance
  - `optimize(returns, *, target_return, risk_free_rate, shrink, long_only, max_weight)`
    — CLARABEL-solver QP via cvxpy; fallback to equal-weight on infeasible status
  - `efficient_frontier(returns, *, n_points, shrink, long_only)` — sweeps 20
    portfolios from min-var to max-return
  - `PortfolioResult` dataclass with `.success`, `.sharpe`, `.summary()`
- `src/meridian/research/walkforward.py`:
  - `make_folds(returns, *, train_size, test_size, step, expanding)` — generates
    expanding or rolling-window walk-forward folds with no look-ahead
  - `evaluate(folds, *, optimize_fn, **optimizer_kwargs)` — runs optimizer on
    each fold's train set, evaluates on test; per-fold Sharpe, ann. return, vol,
    max drawdown; graceful fallback to equal-weight on optimizer error
  - `WalkForwardSummary.summary()` — tabular aggregate + per-fold rows
  - `Fold`, `FoldResult`, `WalkForwardSummary` dataclasses
- `src/meridian/cli/experiment.py` — `experiment` command group:
  - `meridian experiment run <name> [--params JSON] [--window-start DATE]
    [--window-end DATE] [--notes TEXT] [--timeout SECONDS]`
  - `meridian experiment list [--name NAME] [--limit N]`
  - `meridian experiment portfolio --category C [--lookback DAYS]
    [--target-return R] [--no-shrink] [--frontier] [--walk-forward]`
- `experiments/kalshi_fed_pmf/` — first example experiment: prints KXFED implied
  PMF, outputs `{expected_rate, entropy_bits, n_strikes, fomc_date}` as JSON
  metrics; `manifest.yaml` documents params and outputs
- `experiments/arb_snapshot/` — second example experiment: scans all arb
  violations at run time, outputs `{n_partition, n_cross_venue, max_bps}` as JSON
- `pyproject.toml`: added `pandas>=2.2.0`; added `pandas.*` and `yaml.*` to
  mypy `ignore_missing_imports`
- `tests/test_research_portfolio.py` — 16 unit tests: covariance shape/PSD/
  shrinkage, optimizer weight-sum/long-only/variance-minimized/target-return/
  error-on-T<N, max-weight constraint, no-shrink, Sharpe formula, frontier
  monotonicity
- `tests/test_research_walkforward.py` — 14 unit tests: fold count/no-overlap/
  expanding/rolling/raises, fold-result cumulative return/max-drawdown/Sharpe-
  none, evaluate success/empty/error-fallback, summary format

### Changed
- `cli/__main__.py`: registered `experiment` command group
- Phase 6 is now complete — 181 unit tests passing

---

## Phase 5 — 2026-06-18

### Added
- `src/meridian/analytics/fedwatch.py` — implied Fed-rate PMF + CME comparison + event response:
  - `_parse_kxfed_strike(external_id)` — pure: extracts rate from
    `KXFED-26JUN-T3.75` → `Decimal("3.75")`; returns `None` on malformed tickers
  - `build_kalshi_pmf(pool, fomc_date)` — reads latest `p_mid` signals for every
    KXFED contract whose `closes_at` falls on `fomc_date`; normalizes to a
    probability distribution; returns `FedPMF` or `None` if no data
  - `fetch_cme_fedwatch(fomc_date)` — best-effort HTTP GET to CME Group's public
    30-day Fed Funds futures quotes endpoint; derives a 2-strike PMF from the
    implied rate and 25-bps rounding; returns `None` on any failure (ADR-018)
  - `compute_event_response(pool, event_id, *, pre_window, post_window)` — joins
    a `news_events` row to `p_mid` signals for all markets in the same category;
    computes per-market ΔP_mid and variance ratio (post/pre) as an information-
    arrival proxy; returns `EventResponse` or `None` if event not found
  - `FedPMF` dataclass: `expected_rate()`, `entropy()` (Shannon bits), `summary()`
  - `EventResponse` dataclass: `summary()` with per-market table
- `src/meridian/cli/analytics.py`: two new commands in `analytics` group:
  - `meridian analytics fedwatch [--date YYYY-MM-DD] [--cme]` — prints implied
    PMF from Kalshi; `--cme` also attempts CME FedWatch comparison
  - `meridian analytics event-response <EVENT_UUID> [--pre MINUTES] [--post MINUTES]`
    — prints event-response summary for a `news_events` row
- `tests/test_analytics_fedwatch.py` — 25 unit tests: strike parser (standard,
  whole number, missing prefix, too few parts, non-numeric), mean/variance helpers,
  FedPMF expected rate/entropy/summary, `build_kalshi_pmf` (empty→None, single
  contract, normalization, bad ticker skip), `_parse_cme_response` (no quotes,
  wrong month, valid, missing price), `compute_event_response` (event not found,
  no markets, basic delta computation), `EventResponse.summary` format

### Changed
- Phase 5 is now complete — 151 unit tests passing

---

## Phase 4 — 2026-06-18

### Added
- `src/meridian/analytics/microstructure.py` — microstructure analytics engine:
  - `_effective_spread(quotes)` — mean of `ask - bid` across quote ticks; returns
    `Decimal | None`; uses `sum(spreads, Decimal("0")) / len(spreads)` to avoid
    float accumulation
  - `_order_book_imbalance(quotes)` — latest `(bid_size - ask_size) / (bid_size + ask_size)`;
    skips rows with null sizes; returns `None` when total = 0
  - `_kyle_lambda(quotes, trades)` — OLS of `ΔP_mid ~ signed_volume` via
    `np.linalg.lstsq`; signed volume: +size for buy-aggressor, -size for sell;
    returns `None` when fewer than 2 data points can be aligned
  - `_amihud_ratio(trades)` — mean of `|return| / volume` across consecutive
    trade pairs; skips zero-price transitions; returns `None` on < 2 trades
  - `compute_microstructure(pool, market_id, *, window=7d)` — fetches quote and
    trade ticks from the `ticks` hypertable, runs all four metrics, returns
    `MicrostructureMetrics`
  - `simulate_execution(pool, market_id, *, side, target_quantity)` — walks the
    latest `book_snapshots` levels (ask/no ascending for buy; bid/yes descending
    for sell), computes average fill price and slippage; slippage is negated for
    sell orders (positive = received less than best bid)
  - `run_microstructure_sweep(pool, *, market_id, window, write_signals)` —
    sweeps one or all open markets; optionally persists results to `signals`
  - `_write_signals(pool, m)` — writes `effective_spread`, `obi`, `kyle_lambda`,
    `amihud` to `signals` table via `ON CONFLICT DO NOTHING`
  - `MicrostructureMetrics` and `ExecutionEstimate` dataclasses
- `src/meridian/cli/analytics.py`: added `microstructure` command to `analytics`
  group — `meridian analytics microstructure <ticker> [--window DAYS]
  [--simulate-buy QUANTITY] [--simulate-sell QUANTITY] [--write-signals]`
- `tests/test_analytics_microstructure.py` — 19 unit tests covering:
  effective spread (basic, empty, null fields), OBI (positive/negative/zero/empty/
  first-non-null), Amihud (< 2 trades, finite+positive, zero-price guard), Kyle's
  lambda (insufficient data, returns finite float), simulate_execution (single level,
  multi-level price walk, empty book, partial fill, invalid side), `MicrostructureMetrics`
  dataclass construction

### Fixed
- `microstructure.py`: `best_quote` was not converted to `Decimal` before
  arithmetic with `avg_fill`; fixed to `Decimal(str(levels[0]["price"]))`
- `cli/health.py`: pre-existing mypy `[misc]` error on `await client.ping()`
  suppressed with `# type: ignore[misc]` (redis-py stubs declare `ResponseT =
  bool | Awaitable[bool]`; the async client always returns an awaitable)

### Changed
- Phase 4 is now complete — 126 unit tests passing

---

## Phase 3 — 2026-06-18

### Added
- `src/meridian/analytics/arb.py` — no-arbitrage consistency engine:
  - `_check_partition_arb(group_id, label, contracts)` — LP feasibility test
    using `scipy.optimize.linprog` (HiGHS solver); fast sum check + LP
    confirmation for edge cases; fee-adjusted (Kalshi 2¢/side)
  - `PartitionArbResult` — violation_bps, direction (long/short/none),
    depth_feasible flag (requires non-zero resting size on needed side)
  - `run_partition_monitor(pool, threshold_bps, write_signals)` — sweeps all
    `partition` market_groups; writes `arb_violation_bps` rows to `signals`
  - `CrossVenueArbResult` — divergence_bps between two venues
  - `run_cross_venue_monitor(pool, threshold_bps, write_signals)` — sweeps all
    `cross_venue` market_groups with recent p_mid signals; writes
    `cross_venue_divergence_bps` rows to `signals`
  - `KALSHI_FEE_PER_SIDE = Decimal("0.02")` constant
- `src/meridian/cli/arb.py` — `arb` command group:
  - `meridian arb monitor [--threshold-bps N] [--live] [--write-signals]`
    — prints formatted table of violations; `--live` polls every 30 s
  - `meridian arb group-fed [--dry-run]` — discovers ungrouped `KXFED-*`
    Kalshi markets, groups by FOMC date component (e.g. `26JUN`), creates
    `market_groups(type='partition')` rows and links markets
- `cli/__main__.py`: registered `arb` command group
- `pyproject.toml`: added `cvxpy>=1.5.0`; added `scipy.*` to mypy overrides
- `tests/test_analytics_arb.py` — 13 unit tests: no-arb feasible case, long
  arb detected/severity, short arb detected/severity, depth feasibility gate,
  result structure validation

### Changed
- Phase 3 is now complete — 107 unit tests passing

---

## Phase 2 — 2026-06-18

### Added
- `src/meridian/analytics/` package — analytics engine (Phase 2)
- `src/meridian/analytics/signals.py`:
  - `compute_market_signals(pool, market_id)` — reads latest quote tick and
    book snapshot, returns `MarketSignals` (p_bid, p_ask, p_mid, microprice,
    depth_weighted_prob)
  - `run_signal_sweep(pool, *, category)` — sweeps all open markets, writes
    signals to the `signals` table; skips markets with no quote data yet
  - `_midprice`, `_microprice`, `_depth_weighted_prob` — pure/async helpers
  - Uses `Decimal` throughout; converts to `float` only at the
    `signals.value` boundary (DOUBLE PRECISION column)
- `src/meridian/analytics/calibration.py`:
  - `brier_score(p, o)`, `log_loss(p, o)` — pure NumPy implementations
  - `reliability_diagram(p, o, n_bins=10)` — equal-width binning; handles
    `p == 1.0` edge case in the last bin
  - `isotonic_recalibrate(p, o)` — wraps `sklearn.isotonic.IsotonicRegression`;
    non-parametric monotone post-hoc calibration
  - `run_calibration(pool, category, lookback, n_bins)` — queries resolved
    markets + historical p_mid signals, returns `CalibrationResult`
  - `write_calibration_signals(pool, result)` — persists
    `calibration_brier`, `calibration_log_loss`, `calibration_reliability`
    rows to the `signals` table
  - `CalibrationResult.summary()` — human-readable text table
- `src/meridian/cli/analytics.py` — `analytics` command group:
  - `meridian analytics signals [--category X]` — run signal sweep
  - `meridian analytics calibrate [--category X] [--lookback DAYS]
    [--bins N] [--write-signals]` — print calibration report
- `cli/__main__.py`: registered `analytics` command group
- `pyproject.toml`: added `numpy>=1.26.0`, `scipy>=1.13.0`,
  `scikit-learn>=1.5.0` dependencies
- `pyproject.toml`: added `sklearn.*` to `mypy` `ignore_missing_imports`
  overrides
- `tests/test_analytics_signals.py` — 14 unit tests covering pure functions
  and mock-DB async paths: midprice, microprice, depth-weighted probability,
  `compute_market_signals`
- `tests/test_analytics_calibration.py` — 12 unit tests: Brier score symmetry,
  log-loss at clip boundary and uniform prediction, reliability diagram
  binning and edge cases, isotonic recalibration monotonicity and
  non-increase-of-Brier-score on training set

### Changed
- Phase 2 is now complete — 98 unit tests passing

---

## Phase 1d — 2026-06-18

### Added
- `src/meridian/metrics.py` — module-level Prometheus metric objects shared by
  both ingest workers: `ingest_events_total{venue,kind}` (Counter),
  `ingest_lag_seconds{venue}` (Histogram, 10 buckets 50 ms–60 s),
  `ingest_reconnects_total{venue}` (Counter), `ingest_gaps_total{venue}`
  (Counter); `start_metrics_server(port)` wraps
  `prometheus_client.start_http_server` (see ADR-017)
- `prometheus-client>=0.21.0` added to `pyproject.toml` dependencies
- Metric observations wired into `KalshiIngestWorker`:
  - `ingest_events_total` + `ingest_lag_seconds` in `_persist()` after each
    successful write
  - `ingest_reconnects_total` in all three reconnect paths in `run()`
  - `ingest_gaps_total` in `_handle()` when `GapDetector.observe()` returns > 0
- Metric observations wired into `PolymarketIngestWorker`:
  - `ingest_events_total` + `ingest_lag_seconds` in `_persist()`
  - `ingest_reconnects_total` via new `on_reconnect` callback in
    `run_with_reconnect()`
- `ingest/reconnect.py`: added `on_reconnect: Callable[[], None] | None = None`
  parameter to `run_with_reconnect()`; called at every reconnect point
- `cli/ingest.py`: `--metrics-port` option on both `ingest kalshi`
  (default 9091) and `ingest polymarket` (default 9092) — calls
  `start_metrics_server(port)` at startup; pass 0 to disable
- `docker/prometheus/prometheus.yml` — Prometheus scrape config targeting
  both ingest workers at `host.docker.internal:9091` and `:9092`
- `docker/grafana/provisioning/datasources/prometheus.yml` — auto-provisions
  the Prometheus data source in Grafana
- `docker/grafana/provisioning/dashboards/dashboards.yml` — Grafana dashboard
  provider pointing at `/var/lib/grafana/dashboards`
- `docker/grafana/dashboards/meridian-ingest.json` — four-panel dashboard:
  Events/sec (by venue+kind), Ingest Lag p50/p95/p99 (by venue), Reconnects/min,
  Gaps/min
- `docker-compose.yml`: added `prometheus` (port 9090, 30-day retention) and
  `grafana` (port 3000, anonymous viewer, pre-provisioned dashboard) services;
  added `prometheus-data` and `grafana-data` named volumes
- `DECISIONS.md` ADR-017: rationale for using `prometheus_client.start_http_server`

### Changed
- Phase 1d is now complete — both ingest workers emit live Prometheus metrics;
  Grafana dashboard auto-provisions on `make up`

---

## Phase 1c.3 — 2026-06-15

### Added
- `src/meridian/cli/ingest.py` — new `ingest` command group with `kalshi`
  subcommand: `meridian ingest kalshi --tickers T1,T2` runs indefinitely,
  reconnects automatically, and exits cleanly on SIGINT/SIGTERM
- `src/meridian/ingest/worker.py` — major refactor of `KalshiIngestWorker`:
  - `run()` now accepts `stop_event: asyncio.Event | None` instead of
    `seconds: int`; runs until the event is set or the task is cancelled
  - Exponential backoff on reconnect: initial 1 s, max 60 s, jitter ±20%
  - Clean WS close (code 1000) triggers immediate reconnect (no backoff)
  - `reconnects` counter in `IngestStats` tracks all re-connections
  - Each persisted `CanonicalEvent` is published to Redis Streams as
    `XADD kalshi.events * event <json>` when a `redis` client is provided
  - `events_published` counter in `IngestStats`
  - Background REST enrichment: on first sight of a new market ticker,
    schedules `KalshiClient.get_market()` to update `question`, `category`,
    `opens_at`, `closes_at` in place of the `(pending REST sync)` stub
- `tests/test_ingest_worker.py` — five unit tests covering: reconnect after
  server close, stop-event termination, Redis XADD, connection-error backoff,
  and market enrichment DB update (all using local WS server + mock DB pool)
- `IngestStats` extended with `reconnects` and `events_published` fields

### Changed
- `meridian kalshi ingest` command: removed `--seconds` flag; now runs
  indefinitely until interrupted (kept as backward-compatible alias for
  `meridian ingest kalshi`)
- `src/meridian/cli/__main__.py`: registered `ingest` command group

### Fixed
- Pre-existing mypy error in `health.py` (`redundant-cast` on `Awaitable`)

---

## Phase 1c.2 — 2026-06-15

Commit: `e040895`

### Added
- `src/meridian/ingest/worker.py` — `KalshiIngestWorker` orchestrates WS →
  normalize → persist for a fixed-duration run
- `src/meridian/ingest/writer.py` — `TickWriter` routes `CanonicalEvent` to
  `ticks` or `book_snapshots` hypertable with `ON CONFLICT DO NOTHING`
- `src/meridian/ingest/registry.py` — `MarketRegistry` lazily UPSERTs market
  rows on first-seen ticker; in-memory cache avoids redundant DB hits
- `src/meridian/ingest/gap.py` — `GapDetector` tracks per-`sid` sequence
  numbers and emits `signals` rows on forward gaps
- `src/meridian/ingest/stats.py` — `IngestStats` dataclass for run counters
- `migrations/0002_book_delta_and_fractional_sizes.sql`:
  - Adds `book_delta` to `ticks.kind` constraint
  - Extends `book_snapshots.side` to include `yes` / `no` (Kalshi-native)
  - Converts `bid_size`, `ask_size`, `trade_size`, `book_snapshots.size`
    from `INTEGER` to `NUMERIC` (Kalshi reports fractional quantities)

---

## Phase 1c.1 — 2026-06-14

Commit: `983ba9e`

### Added
- `src/meridian/kalshi/ws.py` — `KalshiWebSocketClient`: async context-managed
  WS client; subscribes to `orderbook_delta`, `trade`, `ticker`,
  `market_lifecycle_v2`; yields parsed JSON dicts
- `src/meridian/kalshi/normalize.py` — `normalize_kalshi_message(raw)`:
  routing table of payload builders mapping Kalshi wire types to
  `CanonicalEvent`; returns `None` for control messages
- `CanonicalEvent` extended with `BookDeltaEvent` payload variant (signed
  per-level orderbook delta from the streaming `orderbook_delta` channel)
- `kalshi_market_id(ticker)` — deterministic UUIDv5 market ID derivation
  under fixed namespace `4d2c9b7a-1f3e-4d18-9c4a-7e8f3a2b5c1d`

---

## Documentation rewrite — 2026-06-14

Commit: `b1a1c57`

### Added
- `README.md` complete rewrite: quick start, architecture diagram, project
  structure, commands cheat sheet, tech stack table
- `docs/` tree: `architecture.md`, `cli-reference.md`, `concepts.md`,
  `getting-started.md`, `kalshi.md`, `roadmap.md`, `README.md`

---

## Phase 1b — Kalshi REST client + RSA-PSS auth

Commit: `945a0bd` / `f03e4fe`

### Added
- `src/meridian/kalshi/auth.py` — `KalshiSigner`: RSA-PSS(SHA256) request
  signing; produces three `KALSHI-ACCESS-*` headers
- `src/meridian/kalshi/client.py` — `KalshiClient`: async httpx wrapper;
  methods: `list_markets`, `get_market`, `get_orderbook`, `get_exchange_status`
- `src/meridian/kalshi/endpoints.py` — env-routed REST/WS base URLs for
  `demo` and `prod`
- `src/meridian/kalshi/models.py` — `KalshiMarket`, `KalshiOrderbook`,
  `KalshiMarketStatus`; pydantic aliases map wire names to Python names;
  prices as `Decimal` USD
- `src/meridian/kalshi/errors.py` — `KalshiError / KalshiAuthError / KalshiHttpError`
- `src/meridian/cli/kalshi.py` — CLI commands `kalshi status`, `kalshi markets`,
  `kalshi orderbook`
- Tests: `test_kalshi_auth.py`, `test_kalshi_client.py`

### Changed
- `KalshiMarket` and `KalshiOrderbook` corrected to match actual production
  API response format (wire uses `*_dollars` strings and `orderbook_fp` key,
  not integer cents or bare `orderbook`)

---

## Phase 1a — Canonical event schema + SQL migrations

### Added
- `src/meridian/events.py` — `CanonicalEvent` with discriminated union over
  `QuoteEvent`, `TradeEvent`, `BookEvent`, `StatusEvent`; frozen; Decimal prices
- `src/meridian/db/migrate.py` — forward-only SQL migration runner with
  SHA-256 checksum enforcement; records applied versions in `schema_migrations`
- `migrations/0001_initial_schema.sql` — `venues`, `markets`, `market_groups`,
  `news_events` OLTP tables + `ticks`, `book_snapshots`, `signals` hypertables
- `src/meridian/cli/migrate.py` — `migrate` CLI command
- Tests: `test_events.py`, `test_migrate.py`

---

## Phase 0 — Foundations

### Added
- Repo skeleton: `pyproject.toml` (uv + ruff + mypy + pytest), `Makefile`
- Docker Compose: `timescale/timescaledb:latest-pg16` (port 5433) +
  `redis:7-alpine` (port 6380), both with healthchecks
- `docker/timescale/init.sql` — enables TimescaleDB extension on first boot
- `src/meridian/config.py` — `Settings` via `pydantic-settings`, `MERIDIAN_*` prefix
- `src/meridian/logging.py` — structlog with dev/prod render modes
- `src/meridian/db/postgres.py` — asyncpg pool factory + context manager
- `src/meridian/bus/redis.py` — async Redis client factory + context manager
- `src/meridian/cli/health.py` — `health` command: checks Postgres, TimescaleDB
  extension, Redis
- `.github/workflows/ci.yml` — three parallel jobs: lint+typecheck, unit-tests,
  integration-smoke
- Tests: `test_config.py`, `test_health.py`
- `.env.example` with all documented config knobs
