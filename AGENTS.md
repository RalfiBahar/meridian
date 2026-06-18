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
│   └── microstructure.py # Effective spread, OBI, Kyle's lambda, Amihud, execution simulator
└── cli/
    ├── __main__.py     # click entry point
    ├── health.py       # `health` command
    ├── migrate.py      # `migrate` command
    ├── analytics.py    # `analytics {signals,calibrate,microstructure}` commands
    ├── arb.py          # `arb {monitor,group-fed}` commands
    └── kalshi.py       # `kalshi {status,markets,orderbook}` commands
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

## Current phase status (as of 2026-06-18)

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
| 5–7 | Fed-rate, research, frontend | Planned |

See `ROADMAP.md` for phase details and `TASKS.md` for the prioritized backlog.
