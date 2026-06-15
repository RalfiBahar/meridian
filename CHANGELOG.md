# CHANGELOG

All notable changes to Meridian. Newest first.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

### Planned
- Phase 1d: Polymarket CLOB ingestion + Prometheus/Grafana observability

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
