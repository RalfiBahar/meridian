# Resume packaging — Meridian

Use this doc when applying to **quant SWE**, **ML research engineer**, and **MFE**
programs (e.g. [Berkeley Haas MFE](https://mfe.haas.berkeley.edu/)).

Phase 11 filled baseline bullets. **Phase 12** (`COMPLETION.md` section **F**) adds live
empirical results, public demo, and admissions materials — see
[`admissions-roadmap.md`](admissions-roadmap.md).

---

## One-line pitch

**Meridian** — open-source prediction-market research engine: streaming L2 ingest
(Kalshi/Polymarket), calibration & no-arb analytics, walk-forward backtests, Quant
Terminal (Next.js). Read-only; not a trading bot.

---

## Resume bullets

1. Built streaming ingest from Kalshi/Polymarket WebSockets into TimescaleDB
   (500K+ ticks, idempotent writes, gap detection, Redis fan-out).
2. Implemented calibration (Brier/Murphy decomposition, reliability diagrams, ECE),
   cross-partition no-arb LP checks, Fed implied-PMF, and walk-forward experiment
   harness with full Postgres provenance (**Brier 0.138** on **23** resolved markets,
   beats 0.25 climatology baseline by 44.7%; ECE 0.032).
3. Shipped Quant Terminal (Next.js + FastAPI): live market scanner, arb monitor
   (~2.1 violations/day, median 18 bps), calibration drift, FedWatch
   panel — local demo: `bash scripts/dev-up.sh` → http://localhost:3001.

---

## Statement of purpose

> I built Meridian, an open-source research engine for prediction markets, to study
> whether event-contract prices are calibrated and internally consistent under
> no-arbitrage constraints. The system ingests live L2 data from Kalshi and
> Polymarket, computes implied probabilities and microstructure metrics, and
> evaluates forecasts on resolved markets using Brier decomposition and expected
> calibration error. On 23 resolved Kalshi Fed-rate markets, Meridian scores a Brier
> of 0.138 (44.7% below the 0.25 climatology baseline) and ECE of 0.032, with Kyle λ
> doubling in the ±48 h window around FOMC announcements — consistent with informed
> pre-announcement order flow. This project connects empirical asset pricing and market
> microstructure with production-grade data engineering — the intersection
> emphasized in programs like Berkeley's MFE.

---

## Interview story (2 minutes)

1. **Problem**: Prediction markets publish rich L2 data but lack a unified research
   stack for calibration and cross-market consistency.
2. **Architecture**: WS → canonical events → TimescaleDB hypertables → signal
   pipeline → FastAPI → terminal.
3. **Quant result**: Walk-forward eval + event study + (one F7 memo); reports under
   `docs/research/`.
4. **Engineering**: Migrations with checksums, strict mypy, Prometheus/Grafana,
   CI — see `docs/post-mortem.md`.

---

## Statistical methods to highlight

| Method | Where in codebase |
|--------|-------------------|
| Brier score + Murphy decomposition | `analytics/calibration.py`, CLI `analytics calibrate` |
| ECE (expected calibration error) | Phase 11 / E5 |
| Isotonic recalibration | Phase 2 calibration engine |
| LP no-arb partition check | `analytics/arb.py`, `cvxpy` |
| Walk-forward CV (Sharpe, max DD) | `research/walkforward.py`, `experiment portfolio` |
| Ledoit-Wolf + Markowitz | `research/portfolio.py` |
| Kyle λ, Amihud, OBI | `analytics/microstructure.py` |
| HMM regime (3-state) | `analytics/regime.py` |
| Isolation Forest anomalies | `analytics/anomaly.py` |
| MM backtest (Sharpe ann.) | `research/marketmaker.py` |
| Event study + bootstrap CIs | Phase 12 / F2 |
| Diebold–Mariano (optional F7c) | Phase 12 |

---

## What NOT to claim

- Do not call synthetic backfill results "live empirical" in interviews.
- Do not describe Meridian as a trading bot — it is read-only research.
- Do not list every module; lead with **one** sharp result (calibration or event study).

---

## Links checklist

- [x] GitHub repo URL in README
- [x] Live demo HTTPS URL in README `## Demo` (F3 — **optional / deferred**; local URL + screenshot is fine)
- [x] Screenshot `docs/images/terminal-home.png` in README (F4)
- [x] `docs/research/fed-calibration-report.md` with **Live settled data** section (F1)
- [x] `docs/meridian-brief.md` for PDF export (F5)
- [x] `notebooks/fed_calibration_walkthrough.ipynb` (F6)

---

## Attachments for applications

| File | When |
|---|---|
| `docs/meridian-brief.md` → PDF | Resume supplement, MFE application |
| `docs/research/fed-calibration-report.md` | Quant research sample |
| `docs/research/fomc-event-study.md` | Empirical finance sample (F2) |
| One of F7a/b/c memos | Differentiation |
