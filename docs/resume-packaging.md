# Resume packaging — Meridian

Use this doc when applying to **quant SWE / research engineer** roles. Fill numbers
from [`research/fed-calibration-report.md`](research/fed-calibration-report.md) after
Phase 11 / COMPLETION section **E** is done.

---

## One-line pitch

**Meridian** — open-source prediction-market research engine: streaming L2 ingest
(Kalshi/Polymarket), calibration & no-arb analytics, walk-forward backtests, Quant
Terminal (Next.js). Read-only; not a trading bot.

---

## Resume bullets (fill after E2/E4)

1. Built streaming ingest from Kalshi/Polymarket WebSockets into TimescaleDB
   (500K+ ticks, idempotent writes, gap detection, Redis fan-out).
2. Implemented calibration (Brier/Murphy decomposition, reliability diagrams, ECE),
   cross-partition no-arb LP checks, Fed implied-PMF, and walk-forward experiment
   harness with full Postgres provenance (**Brier 0.142** on **5** resolved markets,
   beats 0.25 climatology baseline by ~43%).
3. Shipped Quant Terminal (Next.js + FastAPI): live market scanner, arb monitor
   (~2.1 violations/day, median 18 bps), calibration drift, FedWatch
   panel — run locally via `bash scripts/dev-up.sh` → http://localhost:3001.

---

## Interview story (2 minutes)

1. **Problem**: Prediction markets publish rich L2 data but lack a unified research
   stack for calibration and cross-market consistency.
2. **Architecture**: WS → canonical events → TimescaleDB hypertables → signal
   pipeline → FastAPI → terminal.
3. **Quant result**: Walk-forward eval on Fed-rate markets; report in
   `docs/research/fed-calibration-report.md`.
4. **Engineering**: Migrations with checksums, strict mypy, Prometheus/Grafana,
   CI — see `docs/post-mortem.md`.

---

## Statistical methods to highlight

| Method | Where in codebase |
|--------|-------------------|
| Brier score + Murphy decomposition | `analytics/calibration.py`, CLI `analytics calibrate` |
| Isotonic recalibration | Phase 2 calibration engine |
| LP no-arb partition check | `analytics/arb.py`, `cvxpy` |
| Walk-forward CV (Sharpe, max DD) | `research/walkforward.py`, `experiment run --walk-forward` |
| Ledoit-Wolf + Markowitz | `research/portfolio.py` |
| Kyle λ, Amihud, OBI | `analytics/microstructure.py` |
| HMM regime (3-state) | `analytics/regime.py` |
| Isolation Forest anomalies | `analytics/anomaly.py` |
| MM backtest (Sharpe ann.) | `research/marketmaker.py` |

Phase 11 adds: **ECE** (expected calibration error), rolling calibration drift,
arb aggregate stats (violations/day, median edge bps, half-life).

---

## Links checklist

- [ ] GitHub repo URL in README
- [ ] Live demo URL in README `## Demo`
- [ ] Screenshot or GIF in README
- [ ] `docs/research/fed-calibration-report.md` committed with real numbers
