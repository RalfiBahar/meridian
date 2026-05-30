# Architecture

How Meridian is laid out, why each component exists, and where each
responsibility lives. Reading this end-to-end takes ~15 minutes.

---

## The thesis

Meridian treats every prediction-market contract as a continuously-updated
probabilistic forecast. The system:

1. **Ingests** live order-book data from Kalshi (primary) and, in a later
   phase, Polymarket.
2. **Normalizes** it into a single venue-agnostic event schema.
3. **Persists** it idempotently into a time-series database.
4. **Computes** derived signals (implied probability, microprice,
   calibration, no-arb violations, ...) as new events arrive.
5. **Surfaces** the signals through a quant-terminal frontend.

The whole system is designed to be defensible in a quant SWE interview: the
data is real, the math is correct, the architecture is the one a real desk
would build.

---

## Component map

```
                       ┌──────────────────────────────────────┐
                       │   Quant Terminal (Next.js — Phase 7) │
                       └────────────────▲─────────────────────┘
                                        │
                       ┌────────────────┴─────────────────────┐
                       │      API Gateway (Phase 7)           │
                       └──┬──────────┬──────────┬──────────┬──┘
                          │          │          │          │
                ┌─────────▼──┐  ┌────▼─────┐ ┌──▼─────┐ ┌──▼────────────┐
                │ Analytics  │  │ Calibrn  │ │ Arb    │ │ Research /    │
                │ (Phase 2)  │  │ (Phase 2)│ │(Phase 3│ │ Backtester    │
                └──────┬─────┘  └────┬─────┘ └──┬─────┘ │ (Phase 6)     │
                       │             │          │       └────────┬──────┘
                       └──────┬──────┴──────────┴───────┬────────┘
                              │                        │
                       ┌──────▼─────────┐      ┌───────▼────────┐
                       │  TimescaleDB   │      │   Postgres     │
                       │   ticks        │      │  markets       │
                       │   book_snapshots│     │  market_groups │
                       │   signals       │     │  news_events   │
                       └──────▲─────────┘      └───────▲────────┘
                              │                        │
                              │      Redis Streams (event bus, Phase 1c)
                              │
                ┌─────────────┴────────────────────────────────────┐
                │   Ingestion workers                              │
                │   - Kalshi WS + REST (Phase 1c, in progress)     │
                │   - Polymarket CLOB  (Phase 1d, planned)         │
                └─────▲─────────────────▲────────────────▲─────────┘
                      │                 │                │
                  Kalshi WS         Polymarket       News/macro
                  + REST            CLOB API         feeds
```

Note both DBs are actually the **same Postgres instance**. TimescaleDB is a
Postgres extension — hypertables and regular tables coexist. We render them
separately above only to make the OLTP-vs-time-series distinction visible.

---

## Existing components (Phase 0 + 1a + 1b)

### `meridian.config` — settings

[src/meridian/config.py](../src/meridian/config.py)

A single `Settings` class via `pydantic-settings`. Reads `MERIDIAN_*` env vars
(prefix-namespaced to avoid collisions), then falls back to a `.env` file.
`Literal[...]` types act as both runtime validation and mypy-time
autocomplete: `MERIDIAN_ENV=staging` is rejected at load time, not at
deployment time at 3am.

The `get_settings()` function returns a fresh instance per call, so tests can
construct a `Settings(...)` with explicit kwargs that *override* any env var
or `.env` content. This is the pydantic-settings priority order:
init-kwargs > env vars > `.env` > defaults.

### `meridian.logging` — structured logging

[src/meridian/logging.py](../src/meridian/logging.py)

Wraps `structlog` with two render modes:

- `env=dev`: pretty-printed console output (`ConsoleRenderer`), colors,
  human-readable.
- otherwise: single-line JSON output (`JSONRenderer`), parseable by Loki,
  Datadog, CloudWatch.

Every log call is `log.info("event.name", key=value, ...)` rather than
formatted strings. Result: logs are *queryable* in any aggregator.
`contextvars.merge_contextvars` automatically binds context across `await`s,
so a trace ID set at request start propagates to every nested task without
manual passing.

### `meridian.events` — canonical event schema

[src/meridian/events.py](../src/meridian/events.py)

The single domain type that every venue-specific parser normalizes into. A
Pydantic v2 discriminated union over four event kinds:

```python
CanonicalEvent(
    venue,                  # 'kalshi' | 'polymarket'
    external_market_id,     # the venue's ticker
    market_id,              # our internal UUID
    sequence_no,            # monotonic per (venue, market_id)
    event_ts,               # the venue's timestamp (UTC, tz-aware)
    ingest_ts,              # our wall clock (UTC, tz-aware)
    payload,                # one of: QuoteEvent | TradeEvent | BookEvent | StatusEvent
)
```

Discriminated union means parsing `{"kind": "trade", ...}` automatically
returns a `TradeEvent`; mypy will refuse `.bid` on a `TradeEvent`.

All event types are **frozen** (`model_config = ConfigDict(frozen=True)`) and
prices are **Decimal**, not float. Floats accumulate rounding error over
billions of ticks; Decimal is exact. We convert to float only at the
analytics boundary where NumPy is involved.

Phase 1c will add a fifth variant, `BookDeltaEvent`, to carry per-level
signed deltas from the WebSocket stream.

### `meridian.db.postgres` — connection pool

[src/meridian/db/postgres.py](../src/meridian/db/postgres.py)

Async Postgres pool via `asyncpg`. Two reasons we chose asyncpg over
SQLAlchemy or psycopg:

1. **Performance.** asyncpg is the fastest Python Postgres driver by 3–5×.
   Phase 1c will do tens of thousands of inserts per second; raw async
   performance matters.
2. **Explicit SQL.** For our schema (a few well-understood tables), raw SQL
   is *easier* to reason about than an ORM. Phase 2 will use window
   functions and CTEs; the ORM would just get in the way.

Exposes two helpers:

- `create_pool(settings)` — caller closes with `await pool.close()`.
- `pool_context(settings)` — async context manager.

### `meridian.db.migrate` — migration runner

[src/meridian/db/migrate.py](../src/meridian/db/migrate.py)

A forward-only SQL migration runner. Numbered `*.sql` files in `migrations/`
are applied in lexicographic order. Each migration is:

- Executed as a single multi-statement script (asyncpg supports this when
  there are no parameters).
- Recorded in `schema_migrations` with version, SHA-256 checksum, and
  timestamp.

The checksum is the load-bearing detail. Once a migration is applied, its
file is **frozen**: editing it after the fact would mean your local DB has
state derived from the original SQL while a teammate's DB or production has
state from the edited version. The runner refuses to proceed if it detects a
mismatch, forcing you to add a new migration with the change instead.

We do **not** use Alembic. Alembic's main value is autogenerate-from-ORM,
which we don't want (we have no ORM, and we explicitly control DDL). Plain
SQL is more predictable and easier to review.

### `meridian.bus.redis` — async Redis client

[src/meridian/bus/redis.py](../src/meridian/bus/redis.py)

Currently just a factory: `create_client` + `client_context`. Phase 1c will
introduce a wrapper that publishes `CanonicalEvent`s to Redis Streams
(`kalshi.events`, `polymarket.events`, `signals.*`) and consumer groups for
downstream analytics services.

### `meridian.kalshi` — Kalshi REST client

The Phase 1b deliverable. See [kalshi.md](kalshi.md) for protocol details.

- `auth.py` — `KalshiSigner`. RSA-PSS signing using `cryptography`. Loads
  the PKCS#1 or PKCS#8 PEM from the path in settings; signs `f"{ts_ms}
  {METHOD}{path}"` with `PSS(MGF1-SHA256, salt=digest_size)`; returns the
  three required headers.
- `endpoints.py` — env-aware URLs. `REST_HOST['prod']`,
  `WS_HOST['demo']`, plus `signed_path()` helper that prepends
  `/trade-api/v2` to relative paths.
- `models.py` — `KalshiMarket`, `KalshiOrderbook`, `KalshiMarketStatus`.
  Uses Pydantic aliases to map the Kalshi wire format (`yes_bid_dollars`,
  `volume_24h_fp`, ...) to Python attribute names (`yes_bid`,
  `volume_24h`, ...) while keeping prices as exact `Decimal` in USD.
- `client.py` — `KalshiClient`, an async context-managed wrapper over httpx
  with `list_markets`, `get_market`, `get_orderbook`, `get_exchange_status`.
  Configurable transport for tests (we use `httpx.MockTransport` rather
  than `respx` to avoid an extra dependency).
- `errors.py` — exception hierarchy: `KalshiError`, `KalshiAuthError`,
  `KalshiHttpError`.

### `meridian.cli` — Click CLI surface

`python -m meridian.cli` runs `__main__.py`, which registers three command
groups today:

- `health` — systems healthcheck (described in [cli-reference.md](cli-reference.md))
- `migrate` — apply pending migrations
- `kalshi {status,markets,orderbook}` — Kalshi REST utilities

Phase 1c will add `kalshi tap` (WS message inspection) and then `ingest
kalshi` (the long-running ingestion worker).

---

## Database schema

The Phase 1a migration (`migrations/0001_initial_schema.sql`) creates these
relations. Note: the OLTP and time-series tables coexist in the same
Postgres instance via the TimescaleDB extension; we render them separately
below for clarity.

### OLTP tables (regular Postgres)

#### `venues`

A small lookup. Seeded with `kalshi` and `polymarket`.

| column | type | purpose |
|---|---|---|
| `id` | SMALLSERIAL PK | join key |
| `code` | TEXT UNIQUE | machine name (`kalshi`, `polymarket`) |
| `name` | TEXT | display name |

#### `market_groups`

Logical groupings used by the Phase 3 no-arbitrage engine.

| column | type | purpose |
|---|---|---|
| `id` | UUID PK | |
| `label` | TEXT | e.g. `"Fed funds rate, June 2026 FOMC"` |
| `group_type` | TEXT | `partition` (mutually exclusive outcomes), `implication` (one event implies another), or `cross_venue` (same logical event across exchanges) |
| `metadata` | JSONB | extensibility |

#### `markets`

Every tradable contract we've seen.

| column | type | purpose |
|---|---|---|
| `id` | UUID PK | our internal id |
| `venue_id` | SMALLINT FK | which exchange |
| `external_id` | TEXT | the venue's ticker, unique within venue |
| `ticker` | TEXT | human-readable identifier |
| `question` | TEXT | the contract's question |
| `category` | TEXT | `fed`, `election`, `weather`, ... |
| `market_group_id` | UUID FK | optional grouping |
| `opens_at`, `closes_at`, `settled_at` | TIMESTAMPTZ | lifecycle timestamps |
| `resolution_status` | TEXT | `open`/`closed`/`settled`/`voided` |
| `settled_value` | SMALLINT | `0` or `1` for binaries |
| `metadata` | JSONB | venue-specific raw fields |

UNIQUE constraint: `(venue_id, external_id)`. Indexed by category, group,
and close time.

We use **internal UUIDs as the primary key** rather than the venue's
external IDs because:
- Venues sometimes recycle IDs.
- Cross-venue arbitrage in Phase 3 needs a stable, venue-agnostic key.
- Joining on UUIDs is fast and keeps the tick table narrow.

#### `news_events`

Timestamped information arrivals (FOMC, CPI, BLS, debates, ...) used by the
Phase 5 event-response analyzer.

### Time-series tables (TimescaleDB hypertables)

#### `ticks`

Every quote/trade/status event lands here. Phase 1c will add `book_delta`
to the kind enum for per-level WS deltas.

| column | type | purpose |
|---|---|---|
| `event_ts` | TIMESTAMPTZ | venue's timestamp (hypertable partition column) |
| `market_id` | UUID FK | which market |
| `sequence_no` | BIGINT | monotonic per `(venue, market_id)` |
| `kind` | TEXT | `quote` / `trade` / `status` (+ `book_delta` in Phase 1c) |
| `bid`, `ask` | NUMERIC(5,4) | top of book in [0, 1] |
| `bid_size`, `ask_size` | INTEGER | depth at top of book |
| `trade_price`, `trade_size` | NUMERIC(5,4), INTEGER | for fills |
| `aggressor` | TEXT | `buy`/`sell` taker side, if reported |
| `payload` | JSONB | venue-specific raw fields |
| `ingest_ts` | TIMESTAMPTZ | our wall clock at write time |

PRIMARY KEY `(market_id, sequence_no, event_ts)` — Timescale requires the
partition column in the PK. The logical idempotency key is `(market_id,
sequence_no)`. Phase 1c uses `INSERT ... ON CONFLICT (market_id,
sequence_no, event_ts) DO NOTHING` to dedup at-least-once delivery into
effectively-exactly-once semantics.

Hypertable partition: 1 day per chunk.

#### `book_snapshots`

L2 depth snapshots: one row per (price level, side) per snapshot event.

| column | type | purpose |
|---|---|---|
| `event_ts` | TIMESTAMPTZ | partition column |
| `market_id` | UUID FK | |
| `sequence_no` | BIGINT | matches the producing event |
| `side` | TEXT | `bid` or `ask` |
| `level` | SMALLINT | 0 = best |
| `price` | NUMERIC(5,4) | level price in [0, 1] |
| `size` | INTEGER | resting size at this level |
| `ingest_ts` | TIMESTAMPTZ | wall clock |

#### `signals`

Generic derived-signal stream. Adding a new signal in Phase 2+ is an
INSERT, not a schema migration.

| column | type | purpose |
|---|---|---|
| `event_ts` | TIMESTAMPTZ | |
| `market_id` | UUID nullable | system-wide signals can omit |
| `signal_type` | TEXT | `microprice`, `p_mid`, `gap_detected`, `arb_violation_bps`, ... |
| `value` | DOUBLE PRECISION | scalar signal value |
| `metadata` | JSONB | extra context |

---

## Tech stack rationale

Brief recap; full reasoning lives in component sections above.

| Layer | Choice | Key reason |
|---|---|---|
| Language | Python 3.12 | Strong async; type-checkable; the user's strongest language |
| Async runtime | asyncio + uvloop | Standard, performant |
| Web framework | FastAPI (Phase 7) | Pydantic schemas serve double duty |
| DB | TimescaleDB | OLTP + time-series in one Postgres instance |
| Driver | asyncpg | Fastest async Postgres driver |
| Migrations | Plain SQL + small runner | No ORM = no Alembic value |
| Event bus | Redis Streams (Phase 1c+) | One binary, persistent, consumer groups |
| HTTP | httpx | Modern, typed, mockable transport |
| Auth crypto | `cryptography` | The standard Python crypto library |
| WebSocket | `websockets` (Phase 1c) | Standard async WS client |
| Logging | structlog | Queryable JSON, contextvars-aware |
| Config | pydantic-settings | Same library as runtime models |
| Tests | pytest + pytest-asyncio | Unit + integration markers |
| Lint | ruff | Replaces flake8 + black + isort |
| Types | mypy strict | Catch numerical bugs early |
| Package mgr | uv | Fast, lockfile-driven |
| Containers | Docker Compose v2 | One file; healthchecks; simple |
| CI | GitHub Actions | 3 parallel jobs |

---

## Event flow (post-Phase-1c)

```
Kalshi matching engine
  │  (event happens, event_ts assigned)
  ▼
Kalshi WS gateway
  │  ~50 ms network from US East
  ▼
Ingestion worker
  │  parse → validate → normalize: <1 ms
  ▼
  ├─► Timescale INSERT (ON CONFLICT DO NOTHING)   ← persistence + dedup
  ├─► Redis XADD (kalshi.events stream)            ← fan-out to consumers
  └─► gap-detected? emit signals row + REST backfill request
```

The dedup boundary is the database UNIQUE constraint. Reconnect-and-replay
is safe: the venue re-sends the last few seconds and every duplicate
INSERT is silently dropped. **At-least-once delivery + idempotent writes =
effectively-exactly-once semantics**, the same pattern Kafka-based
ingestion uses.

---

## Test strategy

Two layers:

- **Unit tests** — no IO. Run in <2s. Cover: schema validation,
  discriminator dispatch, signing correctness, response parsing with a
  mocked HTTP transport, migration discovery and checksum logic.
- **Integration tests** — talk to the live Compose stack. Marked
  `@pytest.mark.integration`. Cover: Postgres reachable + TimescaleDB
  extension loaded, Redis reachable, migration apply + idempotency +
  checksum mismatch detection.

CI runs three parallel jobs:

1. **lint+typecheck** — ruff check, ruff format --check, mypy.
2. **unit-tests** — `pytest -m "not integration"`.
3. **integration-smoke** — boots Compose with `--wait`, runs the
   healthcheck CLI, runs integration tests, tears down volumes.

Result: every push proves the *runtime* works, not just that the code
parses.
