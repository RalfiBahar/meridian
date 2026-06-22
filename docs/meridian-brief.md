# Meridian — Project Brief

**Ralfi Bahar** · [github.com/RalfiBahar/meridian](https://github.com/RalfiBahar/meridian)
`Python · TimescaleDB · FastAPI · Next.js · asyncpg · Redis`

---

## Problem

Prediction markets (Kalshi, Polymarket) publish rich, real-time L2 order-book data
but lack a unified research stack for: (1) calibration measurement against resolved
outcomes, (2) cross-market no-arbitrage consistency checking, and (3) microstructure
signal extraction. This creates an opportunity to apply quantitative finance methods
to a data source that is dense, timestamped, and has observable ground truth.

**Meridian** is a read-only research engine that fills this gap. It is a portfolio
project demonstrating production-grade data engineering alongside empirical
quantitative finance — not a trading bot.

---

## Architecture

```
Kalshi WS ──┐
            ├──► Redis Streams ──► Signal pipeline ──► TimescaleDB ──► FastAPI ──► Next.js
Polymarket ─┘         ↑
                 Canonical event schema (no venue-specific types downstream)
```

### Key layers

| Layer | Technology | Role |
|-------|-----------|------|
| Ingest | Python asyncio + websockets | L2 order-book ticks at ≤1s cadence |
| Schema | `CanonicalEvent` pydantic v2 | Venue-normalised cross-market type |
| Storage | TimescaleDB hypertables | 500K+ ticks; idempotent `ON CONFLICT DO NOTHING` |
| Migrations | Forward-only with SHA-256 checksums | Safe schema evolution |
| Signals | Async pipeline: anomaly / regime / NLP | Written to `signals` table |
| API | FastAPI + uvicorn | REST + WebSocket fan-out; `X-API-Key` auth |
| Frontend | Next.js 15 App Router | Real-time Quant Terminal |
| CI | GitHub Actions | mypy strict, ruff, pytest, coverage gate 80% |

---

## Key results

### Calibration (22 live settled Kalshi Fed-rate markets)

| Metric | Value | vs. Baseline (p=0.5) |
|--------|-------|----------------------|
| Brier score | **0.056** | beats by 77.6% |
| ECE (10-bin) | **0.115** | moderate bin error |
| Data source | Kalshi REST + daily candlesticks | `markets sync-settled` |

Sync via `uv run python -m meridian.cli markets sync-settled --category fed`.
See **Live settled data** in [`fed-calibration-report.md`](research/fed-calibration-report.md).

### No-arbitrage monitoring

| Stat | Value |
|------|-------|
| Violations/day | ~2.1 |
| Median severity | 18 bps |
| LP solver | CVXPY (interior point) |
| Half-life estimate | ~4.2 hours |

Cross-partition LP check runs every 5 minutes across all `fed` markets.

### Microstructure (KXFED-* open markets, 7d rolling)

| Metric | Normal regime | Announcement shock |
|--------|---------------|--------------------|
| Effective spread | 3.9¢ | 6.1¢ |
| Kyle λ | 0.057 ¢/100c | 0.140 ¢/100c (×2.5) |
| Amihud ratio | 2.2×10⁻⁴ | 6.0×10⁻⁴ (×2.7) |

Kyle λ and Amihud both spike in the ±48 h window around FOMC decisions,
consistent with elevated adverse selection from informed participants.
Order book imbalance (OBI) correctly anticipates outcome direction in all
3 observed FOMC events.

### Event study (3 FOMC/CPI events)

| Event | Δp\_mid | 95% CI (bootstrap, n=1000) |
|-------|---------|---------------------------|
| FOMC Sep 2024 (−50bps surprise) | +0.350 | [+0.287, +0.413] |
| FOMC Dec 2024 (−25bps + hawkish) | −0.046 | [−0.121, +0.028] |
| CPI Aug 2024 (below-estimate) | +0.050 | [+0.028, +0.073] |

---

## Engineering highlights

- **361 unit tests, 90.6% coverage** (gate: 80%). `make test` runs in < 30s.
- **mypy strict** across 101 source files; ruff enforced.
- **`CanonicalEvent` invariant**: nothing downstream imports from `kalshi/`. Cross-venue
  composability by construction.
- **Walk-forward harness**: rolling 60d/21d folds with full Postgres provenance; results
  reproducible from `meridian experiment run <name> --walk-forward`.
- **OpenTelemetry + Prometheus/Grafana** observability; gap detection in ingest pipeline.
- **RSA-PSS auth** for Kalshi API; API key auth for local REST/WebSocket.

---

## Demo

```sh
git clone https://github.com/RalfiBahar/meridian
cd meridian
cp .env.example .env   # Kalshi API key + PEM path
make setup
# Open http://localhost:3001
```

Pages: `/` (home) · `/markets` (live scanner) · `/calibration` (Brier/ECE/drift)
· `/arb` (no-arb monitor) · `/fedwatch` (Fed implied PMF vs CME FedWatch)

Screenshot: [`docs/images/terminal-home.png`](images/terminal-home.png)

---

## Research deliverables

| Document | Contents |
|----------|----------|
| [`docs/research/fed-calibration-report.md`](research/fed-calibration-report.md) | Full calibration results with Murphy decomposition |
| [`docs/research/fomc-event-study.md`](research/fomc-event-study.md) | Event study: Δp\_mid, bootstrap CIs |
| [`docs/research/microstructure-memo.md`](research/microstructure-memo.md) | Kyle λ, Amihud, regime-conditional spread |
| [`notebooks/fed_calibration_walkthrough.ipynb`](../notebooks/fed_calibration_walkthrough.ipynb) | Reproducible Jupyter walkthrough |
