# AGENTS.md — AI Coding Agent Guide

This file is for AI coding agents (Claude, Copilot, etc.) working on Meridian.
It documents invariants, conventions, and where to find things.

---

## What this project is

Meridian is a **prediction-market research engine** — a read-only analytics
platform. It ingests live order-book data from Kalshi (and eventually
Polymarket), normalizes it into a canonical event schema, persists it to
TimescaleDB, and computes derived signals (implied probabilities, calibration
scores, no-arbitrage violations, microstructure metrics).

**It does not place orders or move money.** All exchange integrations are read-only.

---

## Tech stack quick reference

| Layer | Tech | Notes |
|---|---|---|
| Language | Python 3.12 | `asyncio` throughout; no threading |
| DB | TimescaleDB (Postgres 16 extension) | port 5433 locally |
| Event bus | Redis 7 Streams | port 6380 locally |
| DB driver | asyncpg | raw SQL; no ORM |
| HTTP client | httpx | async; `MockTransport` in tests |
| WebSocket | websockets | Kalshi WS client |
| Auth | cryptography (RSA-PSS) | Kalshi request signing |
| Models | pydantic v2 | discriminated unions; frozen; Decimal prices |
| Config | pydantic-settings | `MERIDIAN_*` env prefix |
| Logging | structlog | JSON in prod, console in dev |
| CLI | click | `python -m meridian.cli` |
| Tests | pytest + pytest-asyncio | unit (`-m "not integration"`) + integration |
| Lint | ruff | replaces flake8 + black + isort |
| Types | mypy strict | run before every commit |
| Package mgr | uv | `uv sync` to install |
| Containers | Docker Compose v2 | `make up` / `make down` |
| CI | GitHub Actions | 3 parallel jobs |

---

## Repository layout

```
src/meridian/
├── config.py           # Settings via pydantic-settings; MERIDIAN_* env vars
├── logging.py          # structlog setup; get_logger("meridian.module.name")
├── metrics.py          # Prometheus metric objects + start_metrics_server(); shared by workers
├── events.py           # CanonicalEvent + discriminated union (the core type)
├── db/
│   ├── postgres.py     # asyncpg pool: create_pool() / pool_context()
│   └── migrate.py      # forward-only SQL migration runner
├── bus/
│   └── redis.py        # async Redis client factory
├── kalshi/
│   ├── auth.py         # KalshiSigner: RSA-PSS signing → 3 headers
│   ├── client.py       # KalshiClient: async REST wrapper
│   ├── endpoints.py    # env-routed REST + WS URLs
│   ├── models.py       # KalshiMarket, KalshiOrderbook (wire → Python)
│   ├── normalize.py    # raw WS message → CanonicalEvent
│   ├── ws.py           # KalshiWebSocketClient (async context manager)
│   └── errors.py       # KalshiError / KalshiAuthError / KalshiHttpError
├── ingest/
│   ├── worker.py       # KalshiIngestWorker: WS → normalize → persist
│   ├── writer.py       # TickWriter: CanonicalEvent → SQL (ON CONFLICT DO NOTHING)
│   ├── registry.py     # MarketRegistry: lazy UPSERT market rows
│   ├── gap.py          # GapDetector: sequence-number gap → signals row
│   └── stats.py        # IngestStats: counters for one run
├── analytics/
│   ├── __init__.py
│   ├── signals.py        # Signal extractors: p_mid/p_bid/p_ask/microprice/depth_weighted_prob
│   ├── calibration.py    # Brier score, log loss, reliability diagram, isotonic recalibration
│   ├── arb.py            # LP no-arb partition checker + cross-venue divergence monitor
│   ├── microstructure.py # Effective spread, OBI, Kyle's lambda, Amihud, execution simulator
│   ├── fedwatch.py       # Implied Fed PMF, CME FedWatch fetch, event-response analyzer
│   ├── anomaly.py        # Isolation Forest anomaly detector over signals stream (Phase 8)
│   └── regime.py         # Gaussian HMM regime detector: low/medium/high volatility (Phase 8)
├── research/
│   ├── __init__.py
│   ├── experiment.py    # Experiment runner + DB persistence (experiments table)
│   ├── portfolio.py     # Markowitz MV optimizer: Ledoit-Wolf shrinkage, cvxpy CLARABEL
│   ├── walkforward.py   # Walk-forward evaluation harness (no look-ahead)
│   └── marketmaker.py  # Event-driven MM backtest: fill sim, FIFO P&L, Sharpe (Phase 9)
└── cli/
    ├── __main__.py     # click entry point
    ├── health.py       # `health` command
    ├── migrate.py      # `migrate` command
    ├── analytics.py    # `analytics {signals,calibrate,microstructure,fedwatch,event-response}`
    ├── arb.py          # `arb {monitor,group-fed}` commands
    ├── experiment.py   # `experiment {run,list,portfolio}` commands
    └── kalshi.py       # `kalshi {status,markets,orderbook}` commands
experiments/
    kalshi_fed_pmf/     # Experiment: implied Fed PMF from KXFED contracts
    arb_snapshot/       # Experiment: daily arb violation snapshot
migrations/
    0001_initial_schema.sql
    0002_book_delta_and_fractional_sizes.sql
tests/
    conftest.py
    test_config.py
    test_events.py
    test_health.py
    test_kalshi_auth.py
    test_kalshi_client.py
    test_kalshi_normalize.py
    test_migrate.py
```

---

## Key invariants — never violate these

### 1. All prices are `Decimal`, not `float`
Financial prices in `[0, 1]` must use `decimal.Decimal`. Floats silently
accumulate rounding error over millions of ticks. Convert to `float` only
at the NumPy/analytics boundary (Phase 2+).

### 2. All `CanonicalEvent` payloads are frozen and venue-agnostic
`events.py` is the **only** place that defines domain types. Every venue
parser normalizes into `CanonicalEvent` before touching the DB or bus.
Downstream code must not import from `kalshi/` or `polymarket/` directly.

### 3. Migrations are forward-only and checksummed
Once a `.sql` file in `migrations/` is committed and applied, **do not edit it**.
Create a new numbered file instead. The runner enforces SHA-256 checksum
integrity and will refuse to proceed on mismatch.

### 4. Idempotent writes with `ON CONFLICT DO NOTHING`
All `INSERT` statements in `writer.py` and `registry.py` use
`ON CONFLICT DO NOTHING`. Reconnect-and-replay is safe by design.
Do not add `UPSERT` logic or `ON CONFLICT DO UPDATE` unless there is a
very explicit reason.

### 5. `market_id` is a deterministic UUIDv5
`normalize.py::kalshi_market_id(ticker)` derives a stable UUID from the
Kalshi ticker using `KALSHI_UUID_NAMESPACE`. This value must never change
— changing the namespace invalidates all stored foreign keys.

### 6. Tests are split: unit vs integration
Unit tests: no IO, run in <2 s, no Docker required.
Integration tests: marked `@pytest.mark.integration`, require `make up`.
Never import `asyncpg` or `redis` in unit tests without mocking.

---

## How to run things

```sh
make install      # uv sync
make up           # boot TimescaleDB + Redis
make migrate      # apply pending migrations
make health       # verify connectivity
make test         # unit tests only (fast)
make test-all     # unit + integration (requires make up)
make lint         # ruff check
make typecheck    # mypy strict
make check        # lint + typecheck + unit tests
```

---

## Code style rules

- `ruff` enforces formatting and lint (line length 100, double quotes).
- `mypy --strict` must pass. Use `cast()` rather than `# type: ignore` where possible.
- Logging: `log.info("event.name", key=value)` — dot-namespaced event names, structured kwargs.
- No comments explaining *what* the code does. Only explain *why* when the reason is non-obvious.
- No docstrings except one-line module-level docs when the module's purpose isn't obvious from its name.

---

## Adding a new venue

1. Create `src/meridian/<venue>/` mirroring the `kalshi/` structure.
2. Write a `normalize_<venue>_message()` that returns `CanonicalEvent | None`.
3. Seed the venue in `migrations/` with a new numbered `.sql` file (add to `venues` table).
4. Add a `<venue>IngestWorker` following `ingest/worker.py` pattern.
5. Add CLI commands under `cli/<venue>.py`.
6. All canonical events must use the same `CanonicalEvent` model — no venue-specific fields leak downstream.

---

## Adding a new migration

1. Create `migrations/NNNN_description.sql` (next number in sequence).
2. Wrap DDL in `BEGIN; ... COMMIT;`.
3. Run `make migrate` to apply; verify with `make health`.
4. Never edit a migration that has already been applied.

---

## Current phase status (as of 2026-06-19)

| Phase | Description | Status |
|---|---|---|
| 0 | Foundations | Done |
| 1a | Canonical event schema + migrations | Done |
| 1b | Kalshi REST client + RSA-PSS auth | Done |
| 1c | Kalshi WebSocket ingestion worker | Done |
| 1d | Polymarket ingestion + observability | Done |
| 2 | Implied probability + calibration engine | Done |
| 3 | Cross-market no-arb consistency engine | Done |
| 4 | Microstructure analytics + execution simulator | Done |
| 5 | Implied Fed-rate distribution + event-response model | Done |
| 6 | Research framework + portfolio optimizer | Done |
| 7 | Quant Terminal frontend + production polish | Done |
| 8 | Anomaly detection + regime detection (stretch goals) | Done |
| 9 | Simulated market maker (stretch goal) | Done |

## Phase 7 layout (added 2026-06-18)

```
frontend/             Next.js 15 + TypeScript quant terminal
├── src/
│   ├── app/
│   │   ├── layout.tsx          sticky nav (Markets / Arb / Calibration / FedWatch)
│   │   ├── markets/
│   │   │   ├── page.tsx        server component → MarketScannerClient
│   │   │   └── [id]/page.tsx   server component → MarketDetailClient
│   │   ├── arb/page.tsx        server component → ArbMonitorClient
│   │   ├── calibration/page.tsx category-tab calibration dashboard
│   │   └── fedwatch/page.tsx   3-FOMC-meeting PMF panel
│   ├── components/
│   │   ├── MarketScannerClient.tsx  live WS snapshots every 5 s
│   │   ├── MarketDetailClient.tsx   live WS ticks for a single market
│   │   └── ArbMonitorClient.tsx     live WS arb snapshots every 30 s
│   ├── lib/
│   │   ├── api.ts              typed REST wrappers (fetchMarkets, fetchMarket, …)
│   │   └── ws.ts               useWs hook (auto-reconnect WebSocket)
│   └── types/api.ts            TypeScript mirrors of all Pydantic models
├── Dockerfile                  3-stage Next.js build; standalone output
├── fly.toml                    Fly.io config for frontend
└── .env.local.example          MERIDIAN_API_URL / NEXT_PUBLIC_WS_URL / NEXT_PUBLIC_API_KEY

src/meridian/api/    FastAPI gateway
├── app.py           create_app() factory; RateLimitMiddleware; module-level singleton
├── auth.py          require_api_key dependency; get_valid_keys(); WS ?api_key=
├── deps.py          lifespan (pool + redis + hub); get_pool/get_redis/get_hub
├── hub.py           EventHub: Redis Streams → asyncio Queue fan-out
├── models.py        Pydantic v2 response schemas for all endpoints
├── telemetry.py     OTel SDK + FastAPIInstrumentor
└── routes/
    ├── health.py        GET /health
    ├── markets.py       GET/WS /api/v1/markets, GET/WS /api/v1/markets/{id}
    ├── arb.py           GET/WS /api/v1/arb/violations
    ├── calibration.py   GET /api/v1/calibration
    └── fedwatch.py      GET /api/v1/fedwatch

src/meridian/cli/serve.py   meridian serve --host --port --workers --reload

Dockerfile   Backend image (uv + uvicorn 2-worker)
fly.toml     Backend Fly.io config
```

See `ROADMAP.md` for phase details and `TASKS.md` for the prioritized backlog.
