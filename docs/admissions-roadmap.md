# Admissions & portfolio roadmap — Phase 12

Use this doc when preparing Meridian for **quant SWE**, **ML research engineer**, and **MFE**
applications (e.g. [Berkeley Haas MFE](https://mfe.haas.berkeley.edu/)).

Phases 0–11 built the platform. Phase 12 turns it into a **credible portfolio piece**:
one sharp empirical narrative + a public demo admissions committees can click.

---

## What reviewers want

| Audience | Cares about |
|---|---|
| **MFE admissions** | Clear economic question, proper statistics, reproducibility, applied finance framing |
| **Quant SWE** | Production ingest, correctness, tests, architecture under pressure |
| **ML roles** | One strong eval (calibration, forecast comparison, event study) — not ten shallow models |

**Do not** add trading execution. Read-only is a feature.

**Do not** oversell synthetic calibration as live empirical results — label data sources clearly.

---

## Phase 12 goals (see `COMPLETION.md` section **F**)

Work top-to-bottom. Agent loop stops when `bash scripts/check-completion.sh` exits **0**.

### Tier 0 — Ops & visibility (F3, F4)

| ID | Deliverable |
|---|---|
| **F3** | Deploy API + frontend (Fly.io / Railway). Put **live HTTPS URL** in README `## Demo`. |
| **F4** | Screenshot `docs/images/terminal-home.png`; embed in README Demo. |

### Tier 1 — Credibility (F1, F2, F8)

| ID | Deliverable |
|---|---|
| **F1** | **Real** settled-market calibration: ≥20 actually settled Kalshi markets; new **"Live settled data"** section in `docs/research/fed-calibration-report.md` (separate from synthetic backfill). |
| **F2** | `docs/research/fomc-event-study.md` — FOMC/CPI windows, Δp_mid, bootstrap 95% CIs, ≥3 events. |
| **F8** | Live CME FedWatch comparison on `/fedwatch` **or** documented fixture mode in `docs/fedwatch.md`. |

### Tier 2 — Differentiation (F7 — pick exactly ONE)

| Option | Deliverable |
|---|---|
| **F7a** | `docs/research/cross-venue-efficiency.md` — Kalshi ↔ Polymarket linked markets, divergence, half-life |
| **F7b** | `docs/research/microstructure-memo.md` — Kyle λ, Amihud, regime-conditional spread over time |
| **F7c** | Diebold–Mariano forecast test (`microprice` vs `p_mid` vs isotonic-calibrated) in code + short write-up |

### Tier 3 — Packaging (F5, F6, F9)

| ID | Deliverable |
|---|---|
| **F5** | `docs/meridian-brief.md` (2 pages) → optional export to PDF for applications |
| **F6** | `notebooks/fed_calibration_walkthrough.ipynb` — reproducible walkthrough for non-engineers |
| **F9** | Complete `docs/resume-packaging.md`: SOP paragraph, links checklist, CV bullets using **live** numbers |

### Tests (F10)

- `make test` passes after Phase 12 changes.
- ≥6 new unit tests if F7c adds forecast-eval code.

---

## Optional (never blocks COMPLETE)

Section **D** in `COMPLETION.md`: calibration dashboard on purely live settled data (D1),
CME network (D2), deploy polish beyond F3.

---

## CV framing (after F complete)

**Title:** Meridian — Prediction Market Research Engine | Python, TimescaleDB, FastAPI, Next.js

Use bullets from `docs/resume-packaging.md`. Lead with **numbers** (Brier, ECE, ticks, violations/day).

---

## SOP paragraph template (customize in F9)

> I built Meridian, an open-source research engine for prediction markets, to study
> whether event-contract prices are calibrated and internally consistent under
> no-arbitrage constraints. The system ingests live L2 data from Kalshi and
> Polymarket, computes implied probabilities and microstructure metrics, and
> evaluates forecasts on resolved markets using Brier decomposition and expected
> calibration error. This project connects empirical asset pricing and market
> microstructure with production-grade data engineering — the intersection
> emphasized in Berkeley's MFE program.

---

## Agent instructions

1. Run `bash scripts/check-completion.sh`.
2. If exit **1**, work the **first unchecked F item** in `COMPLETION.md`.
3. One F item per session unless the user directs otherwise.
4. Re-run checker before ending; if exit **0**, reply **`MERIDIAN COMPLETE — stopping.`**

See [`AGENT-LOOP.md`](../AGENT-LOOP.md) for the loop start command.
