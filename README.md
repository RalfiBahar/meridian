# Meridian

A prediction-market research engine. Ingests live order-book data from Kalshi
(primary) and Polymarket, extracts implied probabilities, scores
their calibration against realized outcomes, detects no-arbitrage violations
across related markets, and surfaces microstructure signals — over a
streaming event-driven pipeline.

**This is a research platform, not a trading bot.** The system reads exchange
data and computes derived signals. It does not place orders or move money.

---

## Status

**Phases 0–11 are code-complete.** **Phase 12** (admissions packaging for quant SWE / ML / MFE) is active — see [`COMPLETION.md`](COMPLETION.md) section **F**, [`docs/admissions-roadmap.md`](docs/admissions-roadmap.md), and [`AGENT-LOOP.md`](AGENT-LOOP.md).

| Phase | Scope | Status |
|---|---|---|
| 0 | Foundations: repo skeleton, Docker stack, CI, healthcheck | ✓ Done |
| 1a | Canonical event schema + SQL migrations | ✓ Done |
| 1b | Kalshi REST client + RSA-PSS auth | ✓ Done |
| 1c | Kalshi WebSocket ingestion worker | ✓ Done |
| 1d | Polymarket ingestion + Prometheus/Grafana observability | ✓ Done |
| 2 | Implied probability + calibration engine | ✓ Done |
| 3 | Cross-market no-arb consistency engine | ✓ Done |
| 4 | Microstructure analytics + execution simulator | ✓ Done |
| 5 | Implied Fed-rate distribution + event-response model | ✓ Done |
| 6 | Research framework + portfolio optimizer | ✓ Done |
| 7 | Quant Terminal frontend + production polish | ✓ Done |
| 8–10 | Anomaly, regime, market maker, NLP (stretch) | ✓ Done |
| 11 | Resume polish: ECE, arb stats, home page, research report | ✓ Done |
| 12 | Admissions: live calibration, event study, deploy, brief | In progress |
| 11 | Resume polish: ECE, arb stats, home page, research report | In progress |

Full narrative: [docs/roadmap.md](docs/roadmap.md) · [ROADMAP.md](ROADMAP.md)

---

## Demo

**Local:** after `bash scripts/dev-up.sh` — [http://localhost:3001](http://localhost:3001) (home) · [markets](http://localhost:3001/markets) · [calibration](http://localhost:3001/calibration) · [arb](http://localhost:3001/arb) · [fedwatch](http://localhost:3001/fedwatch)

**Public URL:** TBD (see COMPLETION E9 / optional D3 deploy)

**Research report:** [`docs/research/fed-calibration-report.md`](docs/research/fed-calibration-report.md) · **Resume bullets:** [`docs/resume-packaging.md`](docs/resume-packaging.md)

Screenshot: `docs/images/terminal-home.png` (add after Phase 11)

---

## Quick start (60 seconds)

Prerequisites: Docker (with Compose v2), Python 3.12, [`uv`](https://docs.astral.sh/uv/),
plus a Kalshi production account if you want to exercise the live data
features (see [docs/kalshi.md](docs/kalshi.md)).

```sh
git clone https://github.com/RalfiBahar/meridian
cd meridian
cp .env.example .env             # then fill in your Kalshi credentials
make install                     # uv sync
make up                          # boot TimescaleDB + Redis
make migrate                     # apply the schema
make health                      # verify everything is reachable
```

If `make health` reports both Postgres+TimescaleDB and Redis as healthy,
you are ready.

**Full dev stack** (Docker ingest + Grafana + API + frontend + analytics pipeline):

```sh
bash scripts/dev-up.sh           # kill stale processes, reset volumes, boot everything
bash scripts/check-completion.sh # agent stop condition — exit 0 means done
```

See [`COMPLETION.md`](COMPLETION.md) for Phase 12 goals and [`AGENT-LOOP.md`](AGENT-LOOP.md) to start the agent loop.

For a complete first-run walkthrough including credential setup, see
[docs/getting-started.md](docs/getting-started.md).

---

## What works today

```sh
# 1. System healthcheck (Postgres + TimescaleDB + Redis)
make health

# 2. Apply schema migrations idempotently
make migrate

# 3. Verify Kalshi auth handshake against production
uv run python -m meridian.cli kalshi status

# 4. List real markets from Kalshi
uv run python -m meridian.cli kalshi markets --limit 5 --status open

# 5. Pull a real L2 order book (Fed funds futures contract)
uv run python -m meridian.cli kalshi orderbook KXFED-26JUN-T3.75

# 6. Run the full test suite (34 tests, unit + live-DB integration)
make test-all

# 7. Strict type-check (28 source files)
make typecheck

# 8. Lint + format check
make lint
```

Each command and its sample output is documented in
[docs/cli-reference.md](docs/cli-reference.md).

---

## Architecture at a glance

```
                       ┌──────────────────────────────────────┐
                       │   Quant Terminal (Next.js — Phase 7) │
                       └────────────────▲─────────────────────┘
                                        │ WebSocket / REST
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
                       │  ticks         │      │  markets       │
                       │  book_snapshots│      │  market_groups │
                       │  signals       │      │  news_events   │
                       └──────▲─────────┘      └───────▲────────┘
                              │                        │
                              │      Redis Streams (event bus, Phase 1c)
                              │
                ┌─────────────┴────────────────────────────────────┐
                │     Ingestion Workers   ← currently being built  │
                │   - Kalshi WS + REST   (Phase 1c)                │
                │   - Polymarket CLOB    (Phase 1d)                │
                │   normalize → write    (idempotent, gap-aware)   │
                └─────▲─────────────────▲────────────────▲─────────┘
                      │                 │                │
                  Kalshi WS         Polymarket       News/macro
                  + REST            CLOB API         feeds
```

Detailed component descriptions: [docs/architecture.md](docs/architecture.md).

---

## Project structure

```
meridian/
├── docs/                        # All documentation lives here
│   ├── README.md                #   docs index
│   ├── getting-started.md       #   first-time setup walkthrough
│   ├── cli-reference.md         #   every command, with sample output
│   ├── architecture.md          #   system design + db schema
│   ├── kalshi.md                #   Kalshi API details + quirks
│   ├── concepts.md              #   financial concepts implemented
│   └── roadmap.md               #   8-phase plan with status
├── migrations/                  # Forward-only numbered SQL migrations
│   └── 0001_initial_schema.sql
├── src/meridian/
│   ├── config.py                # pydantic-settings (env-driven config)
│   ├── logging.py               # structlog: console in dev, JSON in prod
│   ├── events.py                # canonical CanonicalEvent + payload union
│   ├── db/
│   │   ├── postgres.py          # asyncpg pool factory + context manager
│   │   └── migrate.py           # forward-only migration runner
│   ├── bus/
│   │   └── redis.py             # async Redis client factory
│   ├── kalshi/
│   │   ├── auth.py              # KalshiSigner: RSA-PSS signing
│   │   ├── client.py            # KalshiClient: async REST wrapper
│   │   ├── endpoints.py         # env-routed REST + WS URLs
│   │   ├── models.py            # KalshiMarket, KalshiOrderbook, ...
│   │   └── errors.py            # KalshiError hierarchy
│   └── cli/                     # `python -m meridian.cli ...`
│       ├── __main__.py          # click group + command registration
│       ├── health.py            #   `health`
│       ├── migrate.py           #   `migrate`
│       └── kalshi.py            #   `kalshi {status,markets,orderbook}`
├── tests/                       # 34 tests (6 files; unit + integration)
├── docker/timescale/init.sql    # Enables timescaledb extension on first boot
├── docker-compose.yml           # TimescaleDB + Redis with healthchecks
├── Makefile                     # All developer commands
├── pyproject.toml               # uv-managed deps + tool config
└── .env.example                 # Documented config knobs
```

---

## Commands cheat sheet

```
make help            # show this list
make install         # sync dependencies via uv
make up              # boot postgres+timescale and redis (with --wait)
make down            # stop containers (preserves volumes)
make reset           # stop containers and DELETE volumes (full wipe)
make logs            # tail container logs
make ps              # show container status
make health          # run the healthcheck CLI
make migrate         # apply pending DB migrations
make test            # run unit tests only
make test-all        # run unit + integration tests (requires `make up`)
make lint            # ruff check
make format          # ruff format (modifies files)
make typecheck       # mypy strict
make check           # lint + typecheck + tests, all in one
```

Full descriptions and sample output for each command are in
[docs/cli-reference.md](docs/cli-reference.md).

---

## The `meridian` CLI

```
python -m meridian.cli health                          # systems healthcheck
python -m meridian.cli migrate                         # apply DB migrations
python -m meridian.cli kalshi status                   # auth + exchange status
python -m meridian.cli kalshi markets [--limit N] [--status open]
python -m meridian.cli kalshi orderbook <ticker>
```

See [docs/cli-reference.md](docs/cli-reference.md) for arguments, options, and
sample output.

---

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.12 (asyncio + uvloop) | Strong async ecosystem; type-checkable |
| Web | FastAPI (Phase 7) | Pydantic models double as schemas |
| Database | TimescaleDB (Postgres extension) | One DB for OLTP + time-series |
| Event bus | Redis Streams (Phase 1c+) | Persistent, consumer groups, one binary |
| HTTP | httpx (async) | Modern, typed, swappable transports |
| Crypto | `cryptography` | RSA-PSS for Kalshi auth |
| Logging | structlog | JSON in prod, console in dev |
| Config | pydantic-settings | Same library used for runtime data |
| Tests | pytest + pytest-asyncio | Unit + integration markers |
| Lint | ruff (lint+format) | Fast, replaces flake8/black/isort |
| Types | mypy strict | Catches numerical bugs early |
| Package mgmt | uv | Fast resolver, modern lockfile |

The rationale for each choice is unpacked in [docs/architecture.md](docs/architecture.md).

---

## Repository

- GitHub: <https://github.com/RalfiBahar/meridian> (private)
- CI: GitHub Actions runs on every push — three parallel jobs
  (lint+typecheck, unit tests, integration smoke against a live Compose stack).
