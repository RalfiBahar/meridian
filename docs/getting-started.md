# Getting started

End state of this walkthrough: you have a local stack running, the schema
applied, the Kalshi REST API reachable from your machine, and 34 tests
passing.

Plan: ~15 minutes including Kalshi account setup.

---

## 1. Prerequisites

Install if you don't have them:

- **Docker** (Desktop or Engine) with **Compose v2** — verify with
  `docker compose version`. We rely on v2 syntax (`docker compose`, not
  `docker-compose`).
- **Python 3.12** — exact major.minor. We pin via `.python-version`.
- **[uv](https://docs.astral.sh/uv/)** — modern Python package manager.
  Install with `curl -LsSf https://astral.sh/uv/install.sh | sh`.
- **A Kalshi production account** at <https://kalshi.com>. Free; no funding
  required for read-only API use. (See section 6 for why we use production
  and not the demo environment.)

Optional but recommended:

- **GitHub CLI** (`gh`) — needed if you want to fork and push your own copy.

---

## 2. Clone and install dependencies

```sh
git clone https://github.com/RalfiBahar/meridian
cd meridian
make install
```

`make install` runs `uv sync`, which:

- Creates a `.venv/` in the project root.
- Resolves and installs all dependencies pinned in `uv.lock` (no version drift
  between your machine and CI).
- Installs the local `meridian` package in editable mode, so `import meridian`
  works from anywhere inside the venv.

You don't need to manually activate the venv. Every command in the Makefile
uses `uv run`, which executes inside the venv automatically.

---

## 3. Configure your environment

```sh
cp .env.example .env
```

Open `.env` in your editor. The defaults work for Postgres and Redis (ports
5433 and 6380 — non-default to avoid colliding with any other Postgres/Redis
you might already be running). What you need to fill in is the Kalshi block,
which we'll do in step 6.

| Env var | Default | What it is |
|---|---|---|
| `MERIDIAN_ENV` | `dev` | Runtime: dev/test/prod. Affects logging output. |
| `MERIDIAN_LOG_LEVEL` | `info` | debug/info/warning/error |
| `MERIDIAN_POSTGRES_DSN` | `postgresql://meridian:meridian@localhost:5433/meridian` | DB connection string |
| `MERIDIAN_REDIS_URL` | `redis://localhost:6380/0` | Redis connection |
| `MERIDIAN_KALSHI_ENV` | `demo` | `demo` or `prod` — Kalshi environment |
| `MERIDIAN_KALSHI_ACCESS_KEY` | (empty) | Your Kalshi API key ID |
| `MERIDIAN_KALSHI_PRIVATE_KEY_PATH` | `~/.config/meridian/...` | Path to your RSA private key PEM |
| `MERIDIAN_KALSHI_REQUEST_TIMEOUT` | `10.0` | Per-request timeout in seconds |

---

## 4. Boot the stack

```sh
make up
```

This runs `docker compose up -d --wait`. The `--wait` flag blocks until
*both* containers' healthchecks report healthy — not just until they start.
You'll see:

```
Container meridian-redis      Healthy
Container meridian-timescale  Healthy
```

What's running:

- **`meridian-timescale`** — Postgres 16 with the TimescaleDB extension
  preinstalled. Listens on `localhost:5433`. Data persists in a named
  Docker volume.
- **`meridian-redis`** — Redis 7 with AOF persistence. Listens on
  `localhost:6380`.

Use `make ps` to see status, `make logs` to tail logs, `make down` to stop
(volumes persist), `make reset` to wipe everything.

---

## 5. Apply the schema

```sh
make migrate
```

This runs `python -m meridian.cli migrate`, which applies every pending
forward-only SQL migration in `migrations/` against the database. After it
finishes you should have 8 tables, 3 of them as TimescaleDB hypertables, and
2 venues seeded.

To verify directly:

```sh
docker exec meridian-timescale psql -U meridian -d meridian -c "\dt"
```

Should list:

```
 book_snapshots | market_groups | markets | news_events |
 schema_migrations | signals | ticks | venues
```

The migration runner is **idempotent**: re-running `make migrate` after no
new files have been added is a no-op. Migrations are SHA-256-checksummed on
apply; editing an already-applied migration file raises
`MigrationError`. See [architecture.md](architecture.md#migrations) for why.

---

## 6. Set up your Kalshi credentials

### Why production, not demo

The demo environment is a sandbox for testing *order placement*, not a
mirror of production prices. Its order books are empty. For a read-only
research platform like Meridian, demo is useless — production is required.
Production read-only endpoints (`GET /markets`, `GET /markets/.../orderbook`,
public WebSocket channels) cannot place orders or move money. Only the
`POST /portfolio/orders` endpoint can, and we never call it.

### Generate the keypair

1. Sign in to <https://kalshi.com> (sign up if needed — free, no funding
   required).
2. Account Settings → API Keys → Create a new key.
3. You'll see two values, each with a "copy" button:
   - **API Key ID** — a UUID-like string.
   - **Private Key (PEM)** — shown *exactly once*. Copy it immediately.

### Save the private key

After copying the private key on the website, run in your terminal:

```sh
mkdir -p ~/.config/meridian
pbpaste > ~/.config/meridian/kalshi-prod-private-key.pem
chmod 600 ~/.config/meridian/kalshi-prod-private-key.pem
```

`pbpaste` reads your macOS clipboard. `chmod 600` makes the file readable
only by your user (standard for secrets).

Verify:

```sh
head -1 ~/.config/meridian/kalshi-prod-private-key.pem
# expect:  -----BEGIN RSA PRIVATE KEY-----   (PKCS#1)
# OR:      -----BEGIN PRIVATE KEY-----       (PKCS#8 — also fine)
```

### Set environment variables

Edit `.env` and set:

```
MERIDIAN_KALSHI_ENV=prod
MERIDIAN_KALSHI_ACCESS_KEY=<paste your API Key ID here>
MERIDIAN_KALSHI_PRIVATE_KEY_PATH=~/.config/meridian/kalshi-prod-private-key.pem
```

The path can use `~`; the signer expands it.

---

## 7. Verify end-to-end

Run the full healthcheck:

```sh
make health
```

Expect both Postgres+TimescaleDB and Redis to report `ok=True` with their
version strings.

Run the Kalshi handshake:

```sh
uv run python -m meridian.cli kalshi status
```

Expect:

```
healthcheck.ok                  exchange_active=True trading_active=True
```

That single command proves: your access key is configured, your private key
loads, the RSA-PSS signature is correct, the production endpoint accepts
it, and the response parses.

Pull real markets:

```sh
uv run python -m meridian.cli kalshi markets --limit 5 --status open
```

Pull an actual order book (well-known Fed funds contract):

```sh
uv run python -m meridian.cli kalshi orderbook KXFED-26JUN-T3.75
```

The yes/no level counts and best bid/ask are real production values updated
on every call.

---

## 8. Run the test suite

```sh
make test-all
```

This runs all 34 tests, including the integration tests that talk to the
live Postgres+TimescaleDB and Redis containers. Should finish in ~2 seconds.

```sh
make typecheck    # mypy strict across 28 source files
make lint         # ruff check
```

Both should be clean.

---

## You're ready

Everything works. Common next steps:

- **Browse the CLI**: read [cli-reference.md](cli-reference.md) for full
  detail on every command.
- **Understand the design**: [architecture.md](architecture.md) walks through
  the architecture decisions component by component.
- **Inspect the database**: `docker exec -it meridian-timescale psql -U
  meridian -d meridian` drops you into a `psql` session against your local
  DB.
- **Read the Kalshi notes**: [kalshi.md](kalshi.md) documents the API
  schemas, auth scheme, and quirks we've discovered.

---

## Troubleshooting

### `make up` hangs or containers stay "starting"

Run `make logs` to see what's wrong. Most common: a port conflict on 5433
(Postgres) or 6380 (Redis). Either kill the conflicting process or change the
exposed port in `docker-compose.yml` (and the matching DSN in `.env`).

### `make migrate` fails with `MigrationError: ... was edited after being applied`

Someone (probably you) edited a migration file that's already in
`schema_migrations`. The runner refuses this to prevent silent schema drift.
For local dev: `make reset && make up && make migrate` to start over.
In a shared environment: write a new migration with the change instead.

### `kalshi status` returns `KalshiAuthError: ... ACCESS_KEY ...`

Your `.env` doesn't have `MERIDIAN_KALSHI_ACCESS_KEY` set, or you set it but
your shell still has an old value cached. Try `make health` to confirm
`.env` is being picked up, then re-set the key.

### `kalshi status` returns `KalshiAuthError: ... not an RSA key ...`

You probably saved an SSH-format key or Ed25519 key by mistake. Kalshi
issues PKCS#1 (`-----BEGIN RSA PRIVATE KEY-----`) or PKCS#8
(`-----BEGIN PRIVATE KEY-----`) PEMs. Re-download from Kalshi's account UI.

### `kalshi markets` returns all `yes_bid=None, yes_ask=None`

You're on `MERIDIAN_KALSHI_ENV=demo`. Demo books are empty. Switch to
`MERIDIAN_KALSHI_ENV=prod`. See [kalshi.md](kalshi.md#demo-vs-production).

### `make test-all` fails with `Connection refused`

The Compose stack isn't up. Run `make up` first.

### Anything else

Check `make logs` and the structlog output. Almost every error includes a
specific `error=...` field telling you what's wrong.
