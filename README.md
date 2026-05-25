# Meridian

A prediction market research engine. Ingests live order book data from Kalshi (primary) and Polymarket (secondary), extracts implied probabilities, scores their calibration against realized outcomes, detects no-arbitrage violations across related markets, and surfaces microstructure signals — over a streaming event-driven pipeline.

This is a research platform, not a trading bot.

## Status

**Phase 0** — Foundations. The stack boots, the healthcheck passes, CI is green. Ingestion arrives in Phase 1.

## Local development

Prerequisites: Docker (with Compose v2), Python 3.12, [`uv`](https://docs.astral.sh/uv/).

```sh
cp .env.example .env
make install
make up
make health
```

If `make health` reports both Postgres+TimescaleDB and Redis as healthy, you are ready.

## Commands

```
make install     sync dependencies via uv
make up          boot postgres+timescale and redis
make down        stop containers (preserves volumes)
make reset       stop containers and delete volumes
make health      run the healthcheck CLI
make test        run unit tests
make test-all    run unit + integration tests (requires `make up`)
make lint        ruff check
make format      ruff format
make typecheck   mypy
```
