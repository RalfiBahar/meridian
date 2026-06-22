# COMPLETION.md — Agent loop stop condition

**Read this file before any other task work.**

Implementation phases 0–12 are **code-complete** (`ROADMAP.md`, `TASKS.md`).
Sections **A–E** (operational baseline + polish) and **F** (research deliverables) define the verification gates.

---

## STOP rule (mandatory)

At the **start** of every agent session:

```sh
bash scripts/check-completion.sh
```

| Exit code | Action |
|---|---|
| **0** | Reply exactly: **`MERIDIAN COMPLETE — stopping.`** Do not edit code, open new tasks, or continue the loop. |
| **1** | Work the **first unchecked item** in [Remaining goals](#remaining-goals). Re-run the checker before ending the session. |

When all gates pass (A–C, E, **F**), the checker sets `STATUS: COMPLETE` in this file and exits 0.

**Start agent loop:** see [`AGENT-LOOP.md`](AGENT-LOOP.md).

---

## Current status

```
STATUS: COMPLETE
COMPLETED_AT: 2026-06-22T18:20:20Z
VERIFIED_BY: scripts/check-completion.sh
PHASE: 12 — research deliverables & one-command setup
```

---

## Remaining goals

Work top-to-bottom. Check off each item in this file when done.

### A. Code & docs (no live data required)

- [x] **A1** Fix `meridian experiment list` — `metrics` JSONB must parse to dict.
- [x] **A2** Sync `README.md` status table with `ROADMAP.md`.
- [x] **A3** Ensure `scripts/setup.sh` / `make setup` is committed and referenced from README.
- [x] **A4** Unit tests pass: `make test`.

### B. Seed & pipeline (requires `make setup` or `scripts/dev-up.sh`)

- [x] **B1** Add `scripts/seed-news-events.sql` or `scripts/seed-news-events.sh`.
- [x] **B2** Run `bash scripts/run-full-pipeline.sh` end-to-end with **no traceback**.
- [x] **B3** `meridian experiment list` exits 0.

### C. Data gates (requires ingest running, or seed + backfill)

- [x] **C1** `news_events` count ≥ 10.
- [x] **C2** `ticks` count ≥ 500.
- [x] **C3** At least one `anomaly_score` signal row.
- [x] **C4** At least one `regime_state` signal row.
- [x] **C5** At least one `market_moving_prob` signal row.
- [x] **C6** API health + frontend smoke on `:8000` / `:3001`.

### D. Optional (does not block COMPLETE)

- [ ] **D1** Calibration dashboard has data from live settled sync.
- [ ] **D2** CME FedWatch side-by-side fetch succeeds (network-dependent).
- [ ] **D3** Public deploy — **deferred**; local demo + screenshot (F4) is sufficient.

### E. Polish & statistical depth

See [`docs/research/fed-calibration-report.md`](docs/research/fed-calibration-report.md),
[`docs/meridian-brief.md`](docs/meridian-brief.md), [`TASKS.md`](TASKS.md) Phase 11.

- [x] **E1** Landing home page at `/`.
- [x] **E2** `docs/research/fed-calibration-report.md` filled with Brier, log loss, Murphy, ECE.
- [x] **E3** Settled-market data via `scripts/backfill-real-settled-markets.sh` / `markets sync-settled`.
- [x] **E4** `docs/meridian-brief.md` key results section filled.
- [x] **E5** ECE in `analytics/calibration.py` + API + `/calibration`.
- [x] **E6** Rolling calibration drift UI on `/calibration`.
- [x] **E7** Arb aggregate stats API + UI.
- [x] **E8** `docs/post-mortem.md` performance table + incidents.
- [x] **E9** README `## Demo` section.
- [x] **E10** `meridian experiment export`.
- [x] **E11** FedWatch Kalshi vs CME (or documented fixture mode).
- [x] **E12** Unit tests for ECE + arb stats helpers.

### F. Research deliverables — Phase 12

**Rules:** Label synthetic vs live data clearly in research docs. No trading execution.

#### F — Ops & visibility

- [x] **F4** Screenshot: `docs/images/terminal-home.png`.

#### F — Credibility (empirical)

- [x] **F1** Live settled-market calibration (≥20 Kalshi markets); **Live settled data** in `fed-calibration-report.md`.
- [x] **F2** Event study memo: `docs/research/fomc-event-study.md`.
- [x] **F8** CME FedWatch on `/fedwatch` or fixture mode in `docs/fedwatch.md`.

#### F — Differentiation (complete exactly ONE)

- [ ] **F7a** Cross-venue memo: `docs/research/cross-venue-efficiency.md`.
- [x] **F7b** Microstructure memo: `docs/research/microstructure-memo.md`.
- [ ] **F7c** Forecast eval: Diebold–Mariano + `docs/research/forecast-comparison.md`.

#### F — Packaging

- [x] **F5** Project brief: `docs/meridian-brief.md`.
- [x] **F6** Jupyter walkthrough: `notebooks/fed_calibration_walkthrough.ipynb`.
- [x] **F9** One-command setup documented in README (`make setup`).
- [ ] **F10** `make test` passes; ≥6 unit tests if F7c selected.

---

## How verification works

`scripts/check-completion.sh` tests sections **A–C**, **E**, and **F** automatically.

Run manually:

```sh
bash scripts/check-completion.sh          # full check (needs stack for C gates)
bash scripts/check-completion.sh --code   # A + E + F file gates (no Docker)
```

Section **D** (including **D3 public deploy**) is optional and never blocks COMPLETE.

---

Last session: 2026-06-22 — live calibration sync, `make setup`, removed admissions/resume docs.
