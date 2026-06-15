# ARCHITECTURE.md

Root-level architecture reference. For deeper rationale see `docs/architecture.md`.

---

## System overview

Meridian is a streaming research pipeline for prediction markets:

1. **Ingest** — connect to exchange WebSocket / REST APIs, parse raw wire format
2. **Normalize** — convert venue-specific payloads into `CanonicalEvent`
3. **Persist** — write idempotently to TimescaleDB hypertables
4. **Bus** — publish events to Redis Streams for downstream consumers
5. **Analyze** — compute derived signals (Phase 2+)
6. **Surface** — quant terminal frontend (Phase 7)

---

## Component map

```
┌──────────────────────────────────────┐
│  Quant Terminal  (Next.js — Phase 7) │
└──────────────────▲───────────────────┘
                   │ WebSocket / REST
┌──────────────────┴───────────────────┐
│       API Gateway  (Phase 7)         │
└──┬──────────┬──────────┬──────────┬──┘
   │          │          │          │
┌──▼───┐  ┌───▼──┐  ┌───▼──┐  ┌───▼──────────┐
│Calib │  │Analyt│  │Arb   │  │Research/Back │
│Ph 2  │  │Ph 2  │  │Ph 3  │  │tester Ph 6   │
└──┬───┘  └───┬──┘  └───┬──┘  └───┬──────────┘
   └──────────┴──────────┴─────────┘
                    │
       ┌────────────┴────────────┐
       │                         │
┌──────▼──────────┐   ┌──────────▼──────┐
│  TimescaleDB    │   │   Postgres OLTP  │
│  ticks          │   │  markets         │
│  book_snapshots │   │  market_groups   │
│  signals        │   │  news_events     │
└──────▲──────────┘   └──────────▲──────┘
       │                         │
       │   Redis Streams (bus)   │
       │                         │
┌──────┴─────────────────────────┴──────┐
│         Ingestion Workers             │
│  KalshiIngestWorker  (Phase 1c ✓)    │
│  PolymarketWorker    (Phase 1d)       │
└──────▲───────────────────▲────────────┘
       │                   │
  Kalshi WS+REST     Polymarket CLOB
```

TimescaleDB and Postgres OLTP are the **same Postgres instance** (TimescaleDB
is an extension). Rendered separately to distinguish hypertables from OLTP tables.

---

## Source modules

### `meridian.config`
`pydantic-settings` `Settings` class. Reads `MERIDIAN_*` env vars, falls back to
`.env`. Type-checked at load time — invalid values fail early.

Priority order: `Settings(kwarg)` > env var > `.env` > default.

### `meridian.logging`
`structlog` wrapper. `env=dev` → `ConsoleRenderer`; otherwise `JSONRenderer`.
All log calls use `log.info("event.name", key=value)` — structured, queryable.

### `meridian.events`
The single cross-venue domain type. A frozen pydantic v2 discriminated union:

```
CanonicalEvent
  .venue              Venue.KALSHI | Venue.POLYMARKET
  .external_market_id venue's ticker string
  .market_id          deterministic UUIDv5 (Kalshi: uuid5(NAMESPACE, ticker))
  .sequence_no        monotonic per (venue, market_id)
  .event_ts           venue timestamp (UTC, tz-aware)
  .ingest_ts          our wall clock
  .payload            QuoteEvent | TradeEvent | BookEvent | BookDeltaEvent | StatusEvent
```

All prices are `Decimal`. All models are frozen.

### `meridian.db.postgres`
`asyncpg` pool factory. `create_pool(settings)` or `pool_context(settings)`.
No ORM — raw SQL everywhere.

### `meridian.db.migrate`
Forward-only migration runner. Files in `migrations/NNNN_*.sql` applied
lexicographically. Each run recorded in `schema_migrations` with SHA-256 checksum.
Refuses to proceed if a previously-applied file's checksum changed.

### `meridian.bus.redis`
Async Redis client factory (`create_client` / `client_context`).
Phase 1c.3 will add `XADD` publishing to `kalshi.events` and `signals.*` streams.

### `meridian.kalshi`
- `auth.py` — `KalshiSigner`: signs `f"{ts_ms}{METHOD}{path}"` with RSA-PSS(SHA256).
  Returns the three `KALSHI-ACCESS-*` headers.
- `client.py` — `KalshiClient`: async context manager over `httpx`. Methods:
  `list_markets`, `get_market`, `get_orderbook`, `get_exchange_status`.
- `ws.py` — `KalshiWebSocketClient`: async context manager. Subscribes to
  channels (`orderbook_delta`, `trade`, `ticker`, `market_lifecycle_v2`)
  and yields parsed JSON dicts via `.stream()`.
- `normalize.py` — `normalize_kalshi_message(raw)`: routing table of
  payload builders, returns `CanonicalEvent | None`.
- `models.py` — `KalshiMarket`, `KalshiOrderbook` with pydantic aliases
  mapping wire names (`yes_bid_dollars`) to Python names (`yes_bid`).
- `endpoints.py` — env-routed REST/WS URLs for `demo` and `prod`.
- `errors.py` — `KalshiError / KalshiAuthError / KalshiHttpError`.

### `meridian.ingest`
- `worker.py` — `KalshiIngestWorker.run(seconds=N)`: WS → normalize → persist loop.
- `writer.py` — `TickWriter.write(event)`: routes by payload type to the right
  hypertable; all inserts use `ON CONFLICT DO NOTHING`.
- `registry.py` — `MarketRegistry.ensure_market(id, external_id)`: lazy UPSERT
  market row on first-seen ticker, using in-memory cache to avoid redundant DB hits.
- `gap.py` — `GapDetector.observe(raw)`: tracks per-`sid` sequence numbers;
  emits a `signals` row (`signal_type='gap_detected'`) on forward jumps.
- `stats.py` — `IngestStats` dataclass: counters (received, normalized, rows_written, ...).

### `meridian.cli`
Click CLI: `python -m meridian.cli <command>`.
- `health` — checks Postgres + TimescaleDB extension + Redis, prints version table.
- `migrate` — runs the migration runner, reports applied/skipped counts.
- `kalshi status` — verifies auth handshake against exchange.
- `kalshi markets [--limit N] [--status open]` — list markets table.
- `kalshi orderbook <ticker>` — fetch L2 book and print spread.

---

## Database schema

### OLTP tables (regular Postgres)

| Table | Purpose |
|---|---|
| `venues` | Lookup: `kalshi`, `polymarket` |
| `market_groups` | Logical groupings: `partition`, `implication`, `cross_venue` |
| `markets` | Every seen contract: UUID PK, venue FK, ticker, lifecycle |
| `news_events` | Tagged information arrivals (FOMC, CPI, ...) for Phase 5 |
| `schema_migrations` | Migration audit: version, SHA-256, applied_at |

### TimescaleDB hypertables (1-day chunks)

| Table | PK / partition | Purpose |
|---|---|---|
| `ticks` | `(market_id, sequence_no, event_ts)` | Quotes, trades, status changes, book deltas |
| `book_snapshots` | `(market_id, sequence_no, side, level, event_ts)` | Full L2 depth snapshots |
| `signals` | `event_ts` | Generic derived signal stream (gap_detected, microprice, ...) |

`ticks.kind` values: `quote`, `trade`, `status`, `book_delta`.
`book_snapshots.side` values: `bid`, `ask`, `yes`, `no` (Kalshi-native sides preserved).
Size columns use `NUMERIC` (not `INTEGER`) to support Kalshi fractional quantities.

---

## Event flow (current, Phase 1c)

```
Kalshi exchange
  │
  ▼ WebSocket (~50 ms latency)
KalshiWebSocketClient.stream()
  │
  ▼ normalize_kalshi_message(raw) → CanonicalEvent | None
KalshiIngestWorker._handle()
  │
  ├─► GapDetector.observe(raw)          if gap → INSERT signals
  ├─► MarketRegistry.ensure_market()    if new ticker → INSERT markets
  └─► TickWriter.write(event)           INSERT ticks / book_snapshots
                                         ON CONFLICT DO NOTHING
```

**At-least-once delivery + idempotent writes = effectively-exactly-once semantics.**

Phase 1c.3 will add: `Redis XADD` to `kalshi.events` stream + reconnect/backoff.

---

## CI / deployment

Three parallel GitHub Actions jobs on every push:

| Job | What it does |
|---|---|
| `lint-and-typecheck` | `ruff check`, `ruff format --check`, `mypy` |
| `unit-tests` | `pytest -m "not integration"` |
| `integration-smoke` | `docker compose up --wait`, healthcheck CLI, `pytest -m integration` |

Local ports: TimescaleDB → 5433, Redis → 6380.
