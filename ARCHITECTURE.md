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
│  KalshiIngestWorker     (Phase 1c ✓)  │
│  PolymarketIngestWorker (Phase 1d ✓)  │
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
`XADD` publishing to `kalshi.events` / `polymarket.events` streams (Phase 1c.3, 1d).

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

### `meridian.polymarket` (Phase 1d)
Mirrors `meridian.kalshi`, minus an `auth.py` — the CLOB's read-only market
data needs no authentication (see `docs/polymarket.md`).
- `client.py` — `PolymarketClient`: async context manager over `httpx`. Methods:
  `list_markets`, `get_market`, `get_orderbook`.
- `ws.py` — `PolymarketWebSocketClient`: async context manager over the
  public market channel. Subscribes by `asset_ids` (token IDs); sends a
  `PING` heartbeat every ~10s; flattens batched array frames so `.stream()`
  always yields one message dict at a time.
- `normalize.py` — `normalize_polymarket_message(raw, *, book_state)`:
  returns a **list** of `CanonicalEvent` (a batched `price_change` message
  can update several book levels at once). `PolymarketBookState` is the
  small caller-owned cache that turns Polymarket's absolute-size
  `price_change` updates into the signed `BookDeltaEvent.delta` our schema
  expects (ADR-016). Market identity is keyed on `token_id`, not the parent
  `condition_id` (ADR-015).
- `models.py` — `PolymarketMarket`, `PolymarketToken`, `PolymarketOrderbook`.
- `endpoints.py` — `REST_BASE`, `WS_URL` (single production deployment, no
  env routing).
- `errors.py` — `PolymarketError / PolymarketHttpError`.

### `meridian.ingest`
- `worker.py` — `KalshiIngestWorker.run(stop_event=...)`: WS → normalize → persist loop.
- `polymarket_worker.py` — `PolymarketIngestWorker.run(stop_event=...)`: same
  shape, no `GapDetector` (Polymarket's market channel carries no sequence
  number — see `docs/polymarket.md`).
- `reconnect.py` — `run_with_reconnect()`: shared exponential-backoff
  reconnect loop used by `PolymarketIngestWorker` (Kalshi's worker keeps its
  own copy for now — see the `TASKS.md` cross-cutting follow-up).
- `writer.py` — `TickWriter.write(event)`: routes by payload type to the right
  hypertable; all inserts use `ON CONFLICT DO NOTHING`. Venue-agnostic.
- `registry.py` — `MarketRegistry.ensure_market(id, external_id)`: lazy UPSERT
  market row on first-seen ticker/token, using in-memory cache to avoid
  redundant DB hits. Venue-agnostic (`venue_code` constructor param).
- `gap.py` — `GapDetector.observe(raw)`: tracks per-`sid` sequence numbers;
  emits a `signals` row (`signal_type='gap_detected'`) on forward jumps.
  Kalshi-only — see above.
- `stats.py` — `IngestStats` dataclass: counters (received, normalized, rows_written, ...).

### `meridian.cli`
Click CLI: `python -m meridian.cli <command>`.
- `health` — checks Postgres + TimescaleDB extension + Redis, prints version table.
- `migrate` — runs the migration runner, reports applied/skipped counts.
- `kalshi status` — verifies auth handshake against exchange.
- `kalshi markets [--limit N] [--status open]` — list markets table.
- `kalshi orderbook <ticker>` — fetch L2 book and print spread.
- `polymarket markets [--limit N] [--active-only/--all]` — list markets table.
- `polymarket orderbook <token_id>` — fetch L2 book and print spread.
- `ingest kalshi --tickers ... | ingest polymarket --assets ...` — long-running
  ingest workers; run until SIGINT/SIGTERM.

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
`book_snapshots.side` values: `bid`, `ask`, `yes`, `no` (Kalshi-native sides preserved;
Polymarket uses `bid`/`ask` directly — no migration needed for Phase 1d).
Size columns use `NUMERIC` (not `INTEGER`) to support Kalshi fractional quantities.

---

## Event flow (current, Phase 1d)

```
Kalshi exchange                          Polymarket CLOB
  │                                        │
  ▼ WebSocket (~50 ms latency)             ▼ WebSocket (PING/PONG every 10s)
KalshiWebSocketClient.stream()            PolymarketWebSocketClient.stream()
  │                                        │
  ▼ normalize_kalshi_message(raw)          ▼ normalize_polymarket_message(raw, book_state)
    → CanonicalEvent | None                  → list[CanonicalEvent]
KalshiIngestWorker._handle()              PolymarketIngestWorker._handle()
  │                                        │
  ├─► GapDetector.observe(raw)             │   (no gap detection — see docs/polymarket.md)
  ├─► MarketRegistry.ensure_market()       ├─► MarketRegistry.ensure_market()
  └─► TickWriter.write(event)              └─► TickWriter.write(event)
      INSERT ticks / book_snapshots            INSERT ticks / book_snapshots
      ON CONFLICT DO NOTHING                   ON CONFLICT DO NOTHING
  │                                        │
  ▼ Redis XADD kalshi.events               ▼ Redis XADD polymarket.events
```

Both workers run the same connect → drain → reconnect-with-backoff state
machine (`ingest/reconnect.py:run_with_reconnect`, see `meridian.ingest`
above).

**At-least-once delivery + idempotent writes = effectively-exactly-once semantics.**

---

## CI / deployment

Three parallel GitHub Actions jobs on every push:

| Job | What it does |
|---|---|
| `lint-and-typecheck` | `ruff check`, `ruff format --check`, `mypy` |
| `unit-tests` | `pytest -m "not integration"` |
| `integration-smoke` | `docker compose up --wait`, healthcheck CLI, `pytest -m integration` |

Local ports: TimescaleDB → 5433, Redis → 6380.
