# CLI reference

Every developer command, with arguments, options, sample output, and notes on
what to look for.

The codebase exposes two command surfaces:

- **`make` targets** — developer ergonomics. Wrap `uv run`, `docker compose`,
  and the underlying CLI.
- **`python -m meridian.cli ...`** — the application's own CLI, built on
  Click. Used by `make` targets but also callable directly.

---

## `make` targets

Run `make help` for a self-documenting list. Each target below shows what it
does, what command(s) it wraps, and a typical sample of its output.

### `make install`

Sync project dependencies via uv.

```sh
$ make install
uv sync
```

What it does:

- Creates `.venv/` if missing.
- Resolves dependencies from `pyproject.toml`, then either installs at the
  exact versions pinned in `uv.lock` or regenerates the lockfile if
  `pyproject.toml` changed.
- Installs the `meridian` package itself in editable mode.

When to use: first clone, after pulling new dependencies, or after editing
`pyproject.toml`.

### `make up`

Boot the Compose stack and wait for both containers to report healthy.

```sh
$ make up
Container meridian-redis      Healthy
Container meridian-timescale  Healthy
```

What it does:

- Runs `docker compose up -d --wait`.
- `--wait` blocks until each service's healthcheck passes
  (`pg_isready` for Postgres, `redis-cli ping` for Redis).

When to use: every time you start working, after `make down`, after
rebooting.

### `make down`

Stop the containers but keep the data volumes.

```sh
$ make down
[+] Running 2/2
 ✔ Container meridian-redis      Removed
 ✔ Container meridian-timescale  Removed
```

When to use: at the end of a session if you want a clean process list. Your
data persists; `make up` will resume from where you left off.

### `make reset`

Stop the containers **and delete the volumes**. This wipes your local
database state.

```sh
$ make reset
```

When to use:

- You want a fresh start from an empty schema.
- You suspect a corrupted local state.
- You changed migration files and want to re-apply from zero.

After reset: `make up && make migrate` to rebuild.

### `make logs`

Tail container logs from both services.

```sh
$ make logs
meridian-timescale-1  | 2026-05-28 03:22:18 UTC [1] LOG:  database system is ready to accept connections
meridian-redis-1      | 1:M 28 May 2026 03:22:18.123 * Ready to accept connections
```

`Ctrl-C` to exit.

### `make ps`

Show container status.

```sh
$ make ps
NAME                IMAGE                                   STATUS
meridian-redis      redis:7-alpine                          Up (healthy)
meridian-timescale  timescale/timescaledb:latest-pg16       Up (healthy)
```

### `make migrate`

Apply pending database migrations.

```sh
$ make migrate
healthcheck.start  dir=migrations
migrate.applied    versions=['0001_initial_schema'] count=1
```

When everything is already applied, you'll see `migrate.up_to_date`
instead.

Idempotent. Safe to run any number of times.

### `make health`

Run the system healthcheck CLI. Connects to Postgres, verifies the
TimescaleDB extension is loaded, pings Redis, reports version strings.

```sh
$ make health
healthcheck.start    env=dev
healthcheck.postgres ok=True postgres_version=16.14 timescaledb_version=2.27.1
healthcheck.redis    ok=True redis_version=7.4.9
healthcheck.done     ok=True
```

Exit code: `0` if both healthy, `1` otherwise.

In a CI environment (`MERIDIAN_ENV != dev`), the same output is emitted as
single-line JSON for log aggregators.

### `make test`

Run **unit** tests only. Does not require the stack to be up. Fast inner loop
for development.

```sh
$ make test
collected 34 items / 5 deselected / 29 selected
.............................                                            [100%]
29 passed, 5 deselected in 1.43s
```

The 5 deselected tests are integration tests (marked with
`@pytest.mark.integration`).

### `make test-all`

Run **all** tests, including integration tests that talk to the live stack.

```sh
$ make test-all
collected 34 items
..................................                                       [100%]
34 passed in 2.04s
```

Requires `make up` to have been run first. Integration tests cover:

- Postgres + TimescaleDB extension reachable.
- Redis PING works.
- Migration runner applies cleanly, idempotently, and rejects edited files.

### `make lint`

Run ruff in lint mode.

```sh
$ make lint
All checks passed!
```

Errors out non-zero if any lint rule fires. Configuration is in
`pyproject.toml` under `[tool.ruff.lint]`.

### `make format`

Run ruff in format mode (rewrites files in place).

```sh
$ make format
20 files left unchanged
```

To **check** without rewriting (what CI does), use:

```sh
uv run ruff format --check .
```

### `make typecheck`

Run mypy strict on the project.

```sh
$ make typecheck
Success: no issues found in 28 source files
```

`strict = true` in `pyproject.toml` means all of: no untyped defs, no
implicit Optional, no unused ignores, no redundant casts, no Any returns, etc.

### `make check`

Composite: `make lint && make typecheck && make test`. The full quality gate
for an inner-loop change.

---

## `meridian` CLI

Invoke as `uv run python -m meridian.cli ...` (or via the `make` wrappers).
Click is the underlying framework; `--help` is available everywhere.

```sh
$ uv run python -m meridian.cli --help
Usage: python -m meridian.cli [OPTIONS] COMMAND [ARGS]...

  Meridian command-line interface.

Commands:
  health   Verify Postgres+TimescaleDB and Redis are reachable.
  kalshi   Kalshi API utilities.
  migrate  Apply pending database migrations idempotently.
```

### `meridian.cli health`

Verify Postgres+TimescaleDB and Redis are reachable. Same as `make health`.

```sh
$ uv run python -m meridian.cli health
healthcheck.start    env=dev
healthcheck.postgres ok=True postgres_version=16.14 timescaledb_version=2.27.1
healthcheck.redis    ok=True redis_version=7.4.9
healthcheck.done     ok=True
```

Exit codes: `0` if all healthy, `1` if any check fails. Suitable as a
container-orchestrator liveness probe.

What it actually does (source: [src/meridian/cli/health.py](../src/meridian/cli/health.py)):

1. Opens an asyncpg pool from `MERIDIAN_POSTGRES_DSN`, runs `SHOW
   server_version`, then queries `pg_extension` for the timescaledb row.
   Reports both versions, or the absence of the extension.
2. Opens a Redis async client from `MERIDIAN_REDIS_URL`, runs `PING`, then
   `INFO server` for the version.
3. Catches any exception and reports it in `error=...`. Distinguishes
   "service unreachable" from "service up but timescaledb extension missing".

### `meridian.cli migrate`

Apply pending forward-only SQL migrations against the database.

```sh
$ uv run python -m meridian.cli migrate
migrate.start         dir=migrations
migrate.applied       versions=['0001_initial_schema'] count=1
```

Options:

- `--dir PATH` — migrations directory (default: `./migrations`).

Behavior:

- Creates `schema_migrations` if missing.
- Discovers `*.sql` files in `--dir`, sorted lexicographically.
- For each unapplied version: executes the SQL in a connection, records the
  version + SHA-256 checksum.
- For each already-applied version: verifies its checksum matches the file
  on disk. If not, raises `MigrationError`.
- Exit codes: `0` on success, `1` on error (e.g. checksum mismatch).

### `meridian.cli kalshi status`

Verify Kalshi auth + fetch the exchange status. The first command you should
run after configuring Kalshi credentials.

```sh
$ uv run python -m meridian.cli kalshi status
kalshi.status.ok    exchange_active=True trading_active=True
```

What it proves end-to-end:

- Your access key is set.
- Your private key file loads as a valid RSA key.
- The RSA-PSS signature is computed correctly.
- The Kalshi production (or demo) endpoint accepts the signature.
- The response parses.

If anything goes wrong:

```sh
kalshi.status.failed  error="Kalshi GET /trade-api/v2/exchange/status returned 401: ..."
```

Exit codes: `0` on success, `1` on auth or HTTP error.

### `meridian.cli kalshi markets`

List markets, newest first. Equivalent to `GET /trade-api/v2/markets` on the
Kalshi REST API.

```sh
$ uv run python -m meridian.cli kalshi markets --limit 5 --status open
market                ticker=KXFED-26JUN-T3.75 status=active yes_bid=0.0200 yes_ask=0.0300 ...
market                ticker=KXFED-26JUN-T3.50 status=active yes_bid=0.9600 yes_ask=0.9700 ...
...
kalshi.markets.summary count=5 next_cursor=Cgw...
```

Options:

- `--limit N` (default: 10) — page size, max 1000 per Kalshi.
- `--status STRING` — filter by status. **Kalshi-vocabulary**: `unopened`,
  `open`, `closed`, `settled`, or comma-separated combinations. (Note that
  the response `status` field uses a different enum:
  `initialized|active|closed|settled|deactivated`. See
  [kalshi.md](kalshi.md#status-filter-vocabulary) for why.)

What to look for:

- `yes_bid` / `yes_ask` — best top-of-book on the YES side, as exact Decimal
  USD in `[0,1]`. `None` if no resting orders on that side.
- `last` — last trade price.
- `volume_24h` — 24-hour trading volume (in number of contracts; fractional
  trading is supported).
- `next_cursor` — pass via `--cursor` (if exposed) on the next call to
  paginate.

### `meridian.cli kalshi orderbook <ticker>`

Fetch the L2 order book for one market.

```sh
$ uv run python -m meridian.cli kalshi orderbook KXFED-26JUN-T3.75
kalshi.orderbook  ticker=KXFED-26JUN-T3.75
                  yes_levels=2 no_levels=50
                  yes_best_bid=0.0200 yes_best_ask=0.0300
                  yes_spread=0.0100
                  yes_total_size=291532.36 no_total_size=534792.25
```

Arguments:

- `<ticker>` — Kalshi market ticker, e.g. `KXFED-26JUN-T3.75`.

What to look for:

- `yes_levels` / `no_levels` — count of price levels with resting orders on
  each side. More levels = deeper book.
- `yes_best_bid` / `yes_best_ask` — top of book on the YES side. The ask is
  *derived* as `1 - max(no_bid)`, since Kalshi quotes both sides as bids.
- `yes_spread` — `ask - bid`. The market's uncertainty about its own price.
  Tight spread + high depth = high-confidence market.
- `yes_total_size` / `no_total_size` — sum of contracts across all levels
  on each side. Total liquidity proxy.

The full L2 data lives in the underlying `KalshiOrderbook` model — you can
script directly against it (see [concepts.md](concepts.md#implied-probability-from-an-order-book)).

---

## Direct database access

When you need to inspect state:

```sh
docker exec -it meridian-timescale psql -U meridian -d meridian
```

Useful queries while the schema is empty (pre-Phase-1c):

```sql
\dt                                                        -- list tables
SELECT version, applied_at FROM schema_migrations;          -- migration history
SELECT code, name FROM venues;                              -- seeded venues
SELECT hypertable_name, num_chunks FROM
  timescaledb_information.hypertables;                      -- hypertable status
```

Once ingestion is running (Phase 1c+):

```sql
-- recent ticks
SELECT event_ts, market_id, sequence_no, kind, bid, ask
FROM ticks
ORDER BY event_ts DESC
LIMIT 20;

-- depth snapshot at a specific time
SELECT side, level, price, size
FROM book_snapshots
WHERE market_id = '<uuid>'
  AND event_ts <= '2026-05-28T03:35:00Z'
ORDER BY event_ts DESC, side, level
LIMIT 50;

-- signal stream
SELECT event_ts, signal_type, value, metadata
FROM signals
ORDER BY event_ts DESC
LIMIT 50;
```

---

## Logging output

All commands emit structured logs via `structlog`. Two render modes,
controlled by `MERIDIAN_ENV`:

- `dev` (default): pretty-printed colored console output, one event per
  line, fields shown as `key=value` pairs.
- Anything else: single-line JSON per event. Suitable for Loki/Datadog/
  CloudWatch ingestion.

Toggle with:

```sh
MERIDIAN_ENV=prod uv run python -m meridian.cli kalshi status
```

The same fields are present in both modes — only the formatter differs.

---

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Application-level failure (auth, HTTP, validation, migration mismatch) |
| 2 | Click usage error (bad flag, missing arg) |
| >2 | Python runtime error |

CI relies on these: a non-zero exit fails the relevant job.
