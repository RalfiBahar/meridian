# DECISIONS.md — Architectural Decision Records

Key technical decisions made in this project, with rationale.
These are things that would be non-obvious to a new contributor or AI agent.

---

## ADR-001: Python 3.12 + asyncio (not Go, not Node)

**Decision**: Python 3.12 with `asyncio` and `uvloop` as the async runtime.

**Why**: Python is the dominant language for quant research (NumPy, pandas,
scipy, cvxpy). By Phase 2+ the same process that ingests data also runs
analytics; keeping one language avoids an RPC boundary. `asyncio` handles
thousands of concurrent WebSocket connections and DB queries without threads.
The `websockets`, `asyncpg`, and `httpx` async libraries are mature.

---

## ADR-002: TimescaleDB over InfluxDB / ClickHouse / DuckDB

**Decision**: TimescaleDB (PostgreSQL 16 extension) for all persistence.

**Why**: TimescaleDB lets us keep OLTP tables (`markets`, `venues`,
`market_groups`) and time-series hypertables (`ticks`, `book_snapshots`,
`signals`) in the **same Postgres instance**. One connection pool, one
migration system, one backup strategy. InfluxDB requires a separate service
and lacks relational joins. ClickHouse is OLAP-only; we need foreign keys
for the markets dimension table. DuckDB is in-process and doesn't support
concurrent ingestion + analytics workloads well at this scale.

---

## ADR-003: asyncpg over SQLAlchemy / psycopg3

**Decision**: `asyncpg` with raw SQL; no ORM.

**Why**: asyncpg is 3–5× faster than psycopg3 async and significantly faster
than SQLAlchemy async. Phase 1c targets tens of thousands of inserts per second.
Our schema is simple and stable — an ORM adds no value but would obscure the
`ON CONFLICT DO NOTHING` pattern, the `executemany` batch insert, and the
TimescaleDB `create_hypertable()` DDL calls. SQL is more reviewable.

---

## ADR-004: Forward-only migrations with SHA-256 checksums (not Alembic)

**Decision**: Custom forward-only migration runner in `db/migrate.py`. No Alembic.

**Why**: Alembic's main value is `--autogenerate` from ORM metadata, which we
don't have. Our plain-SQL files are simpler to review, more portable, and
explicitly communicate intent. The SHA-256 checksum guard prevents "edit the
migration after applying it" mistakes — a class of bug that has caused data
inconsistencies in production at other projects. New migrations are always
additive (a new numbered file); there is no concept of a rollback migration.

---

## ADR-005: Redis Streams over Kafka

**Decision**: Redis 7 Streams as the event bus.

**Why**: Kafka requires a JVM runtime, a Zookeeper (or KRaft) cluster, and
non-trivial ops overhead. Redis is already in the stack as a cache and is a
single binary. Redis Streams support consumer groups, persistent delivery, and
`XADD` / `XREAD` semantics — everything needed for fan-out from the ingest
workers to future analytics consumers. For research-scale throughput (thousands
of events/second, not millions), Redis is adequate. If throughput grows, the
bus interface is narrow enough to swap.

---

## ADR-006: Pydantic v2 discriminated unions for `CanonicalEvent`

**Decision**: `events.py` uses a Pydantic v2 discriminated union over a
`Literal[kind]` discriminator field, with all payload types frozen.

**Why**: The discriminated union means mypy and Pydantic's validator
automatically route `{"kind": "trade", ...}` to `TradeEvent` with zero
branching code. Frozen models guarantee that events are immutable once
created — safe to share across async tasks. `extra="forbid"` on payload
models catches typos and schema drift at parse time rather than silently
dropping fields.

---

## ADR-007: `Decimal` (not `float`) for all prices

**Decision**: All prices are `decimal.Decimal`; size/quantity fields are also
`Decimal` (to support Kalshi's fractional quantities like `"19.50"`).

**Why**: Binary IEEE-754 floats accumulate rounding error. Over billions of
ticks, `0.1 + 0.2 != 0.3` causes incorrect calibration scores and arb
computations. `Decimal` is exact. We convert to `float64` only at the NumPy
analytics boundary (Phase 2+) where the math explicitly assumes continuous
approximation.

---

## ADR-008: RSA-PSS for Kalshi authentication

**Decision**: `KalshiSigner` implements RSA-PSS(SHA256) request signing.

**Why**: Required by Kalshi's production API. The signature covers
`f"{ts_ms_unix}{METHOD_UPPER}{/trade-api/v2/path}"`. Using the
`cryptography` library (not `rsa` or `PyJWT`) because it is the standard
Python crypto library, supports PKCS#1 and PKCS#8 PEM formats, and has
no hidden float arithmetic. The key is loaded once from disk and reused
for every request (no repeated PEM parsing).

---

## ADR-009: Deterministic UUIDv5 for market IDs

**Decision**: `normalize.py::kalshi_market_id(ticker)` derives the market UUID
via `uuid5(KALSHI_UUID_NAMESPACE, ticker)` — not a DB sequence or random UUID.

**Why**: Ingestion workers are stateless; they must assign the same `market_id`
without a DB round-trip for every tick. UUIDv5 is deterministic: the same
ticker always produces the same UUID across processes, restarts, and replays.
The namespace `4d2c9b7a-...` is a fixed constant — changing it would
invalidate all `ticks` and `book_snapshots` foreign keys in the DB.

The `MarketRegistry` then lazily ensures the matching row in `markets` exists
(INSERT ... ON CONFLICT DO NOTHING) before ticks reference it via FK.

---

## ADR-010: `ON CONFLICT DO NOTHING` for idempotent ingestion

**Decision**: All tick/book/signal inserts use `ON CONFLICT DO NOTHING`
against the table's primary key.

**Why**: WebSocket connections drop and reconnect. The Kalshi WS protocol
re-sends the last few seconds of messages on reconnect. Without idempotency,
reconnect-and-replay would create duplicate rows. With `ON CONFLICT DO NOTHING`,
at-least-once delivery is combined with idempotent writes to achieve
effectively-exactly-once semantics — the same pattern used by Kafka-based
ingestion pipelines.

---

## ADR-011: `uv` for package management

**Decision**: `uv` (Astral) for dependency resolution and virtual env management.

**Why**: `uv` resolves and installs orders-of-magnitude faster than `pip` or
`poetry`. The lockfile (`uv.lock`) is deterministic and human-readable.
`uv sync --frozen` in CI is fast enough that caching is optional. `hatchling`
is the build backend (PEP 517) — the simplest standard option.

---

## ADR-012: `ruff` for lint + format

**Decision**: `ruff` replaces flake8, black, isort, and pyupgrade as a single tool.

**Why**: One tool, one config section in `pyproject.toml`, 10–100× faster than
running the individual tools. The `ruff format` pass enforces black-compatible
style. Selected rule sets include `B` (bugbear), `UP` (pyupgrade), `ASYNC`
(async best practices), `SIM` (simplify), `C4` (comprehensions). This catches
real bugs — not just style — at lint time.

---

## ADR-013: `mypy --strict` from day one

**Decision**: `mypy` in strict mode, enforced in CI.

**Why**: Prediction-market analytics is numerically sensitive. Type errors
(e.g., treating a `Decimal` as a `float`, accessing `.bid` on a `TradeEvent`)
become wrong numbers in backtests, not runtime crashes. Strict mode costs a
few extra annotations but eliminates an entire class of bugs. The `asyncpg`
stubs are missing, covered by `ignore_missing_imports = true` in `pyproject.toml`.

---

## ADR-014: Separate `demo` and `prod` Kalshi environments

**Decision**: `kalshi_env: Literal["demo", "prod"]` routes to different
REST/WS base URLs. Default is `"demo"` in `Settings`.

**Why**: Kalshi provides a sandbox (`demo.kalshi.com`) that mirrors the production
API but uses play money. Running against demo by default prevents accidental
production API calls during development. The `endpoints.py` routing is explicit
and type-checked: adding a new env requires updating the `Literal` type, which
mypy enforces everywhere the value is used.

---

## ADR-015: Polymarket `market_id` keyed on `token_id`, not `condition_id`

**Decision**: `polymarket_market_id(token_id)` derives the market UUID from
the CLOB **token ID** (`uuid5(POLYMARKET_UUID_NAMESPACE, token_id)`), not
from the higher-level `condition_id`.

**Why**: Polymarket's order book, WS subscription (`assets_ids`), and REST
`/book` endpoint are all keyed by `token_id` — each outcome of a market
(Yes, No, or more for categorical markets) trades on its own independent
book. `condition_id` is the *question*, grouping N tokens; it has no order
book of its own. Treating `token_id` as the identity unit keeps the same
"one `external_market_id` = one order book = one row in `markets`" shape
Kalshi already uses (where `ticker` is the order-book unit), so
`ingest/registry.py`, `ingest/writer.py`, and the `ticks`/`book_snapshots`
schema all work unmodified for the new venue. `condition_id` and the
human-readable `question`/`outcome` are stored as REST-enriched metadata on
the `markets` row, the same way Kalshi's `event_ticker` is metadata rather
than identity. See `docs/polymarket.md`.

---

## ADR-016: Caller-supplied previous-size for Polymarket book deltas

**Decision**: `normalize_polymarket_message()` stays a pure function of its
inputs. For the `price_change` message type (which reports the *absolute*
post-update size at a book level, not a signed increment), the caller
passes in the previous size via a small stateful `PolymarketBookState`
cache and the normalizer computes `delta = new_size - previous_size`.

**Why**: Our `BookDeltaEvent.delta` is defined venue-agnostically as a
signed increment (`events.py`), matching Kalshi's native `delta_fp` wire
field exactly. Polymarket's wire protocol has no equivalent — `price_change`
gives the new resting size outright. Computing the increment requires
remembering the previous size, i.e. state. Rather than make
`normalize_kalshi_message`/`normalize_polymarket_message` asymmetric (one
pure, one stateful-by-hidden-global), we keep both normalizers pure and
push the *minimum* necessary state into an explicit, testable, narrowly-
scoped object (`PolymarketBookState`) that the ingest worker owns —
the same pattern already used for `GapDetector`'s per-`sid` sequence
tracking. The state resets whenever a fresh `book` snapshot arrives, bounding
drift from any missed message (see "No gap detection" in
`docs/polymarket.md`).

---

## ADR-017: `prometheus_client.start_http_server`, not a custom aiohttp app

**Decision**: The `/metrics` endpoint (Phase 1d) is `prometheus_client`'s
built-in `start_http_server()`, called once at ingest-CLI startup, not a
hand-rolled `aiohttp`/`http.server` route inside the ingest worker.

**Why**: `start_http_server()` already does exactly what TASKS.md 1d-g asks
for — a minimal HTTP server in a background thread serving `/metrics` —
and it's the library's own supported way to expose collectors registered
in the default `CollectorRegistry`. Writing a second HTTP server (aiohttp
or stdlib `http.server`) to do the same job would be net-new code with no
behavioral difference, just to avoid a one-line dependency call. If
Meridian later needs a real HTTP surface (health checks, readiness probes,
the Phase 7 API gateway), that's the point to introduce a proper ASGI app;
metrics alone don't justify it yet.

---

## ADR-018: CME FedWatch fetch is best-effort with silent failure

**Decision**: `fetch_cme_fedwatch()` (Phase 5) uses `httpx` to attempt a GET
against CME Group's public futures quotes endpoint. Any network failure,
non-200 response, or parse error returns `None` instead of raising.

**Why**: CME does not publish a stable, versioned public API contract for
FedWatch probabilities; the endpoint format can change without notice. The
Kalshi KXFED PMF is Meridian's primary data source — CME is only a
cross-validation signal. Making the CLI fail or block on an optional
comparison would degrade the user experience. The `--cme` flag clearly
communicates that this is best-effort; users who need CME data reliably can
subscribe to CME DataMine and replace the fetch with a file-based loader.
