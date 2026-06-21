# COMPLETION.md — Agent loop stop condition

**Read this file before any other task work.**

Implementation phases 0–11 are **code-complete** (`ROADMAP.md`, `TASKS.md`).
**Phase 12** (admissions & portfolio packaging) is the **active agent target** — see
[`docs/admissions-roadmap.md`](docs/admissions-roadmap.md) and section **F** below.

Sections **A–E** (operational baseline + resume polish) are done. Work **F** top-to-bottom.

---

## STOP rule (mandatory)

At the **start** of every agent session:

```sh
bash scripts/check-completion.sh
```

| Exit code | Action |
|---|---|
| **0** | Reply exactly: **`MERIDIAN COMPLETE — stopping.`** Do not edit code, open new tasks, or continue the loop. |
| **1** | Work the **first unchecked item** in [Remaining goals](#remaining-goals) — section **F** first if any F item is open; otherwise A–E. Re-run the checker before ending the session. |

When all gates pass (A–C, E, **F**), the checker sets `STATUS: COMPLETE` in this file and exits 0.

**Start agent loop:** see [`AGENT-LOOP.md`](AGENT-LOOP.md).

---

## Current status

```
STATUS: COMPLETE
COMPLETED_AT: 2026-06-21T23:36:43Z
VERIFIED_BY: scripts/check-completion.sh
PHASE: 12 — admissions & portfolio packaging
```

Sections **A–E** passed 2026-06-21. Section **F** is open.

---

## Remaining goals

Work top-to-bottom. Check off each item in this file when done.

### A. Code & docs (no live data required)

- [x] **A1** Fix `meridian experiment list` — `metrics` JSONB must parse to dict (currently crashes with `AttributeError` when asyncpg returns a string).
- [x] **A2** Sync `README.md` status table with `ROADMAP.md` (phases 0–10 Done; link here and to `scripts/dev-up.sh`).
- [x] **A3** Ensure `scripts/dev-up.sh` is committed and referenced from README quick start.
- [x] **A4** Unit tests pass: `make test` (or `uv run pytest -m "not integration"`).

### B. Seed & pipeline (requires `make up` or `scripts/dev-up.sh`)

- [x] **B1** Add `scripts/seed-news-events.sql` or `scripts/seed-news-events.sh` — insert ≥10 `news_events` rows (FOMC/CPI labels, `category` matching open markets e.g. `fed`, `weather`).
- [x] **B2** Run `bash scripts/run-full-pipeline.sh` end-to-end with **no traceback** (skips with human-readable messages are OK for calibration until settled markets exist).
- [x] **B3** `meridian experiment list` exits 0 and prints experiment rows.

### C. Data gates (requires ingest running ≥1 h, or seed + backfill)

- [x] **C1** `news_events` count ≥ 10.
- [x] **C2** `ticks` count ≥ 500.
- [x] **C3** At least one `anomaly_score` signal row **or** `meridian analytics anomaly --write-signals` completes without "Insufficient signal data".
- [x] **C4** At least one `regime_state` signal row **or** `meridian analytics regime --write-signals` completes without "Insufficient signal data".
- [x] **C5** At least one `market_moving_prob` signal row **or** `meridian analytics nlp-tag --write-signals` completes without "Not enough training data".
- [x] **C6** API health + frontend smoke: `curl -sf localhost:8000/health` and `curl -sf -o /dev/null -w '%{http_code}' localhost:3001/markets` returns 200 (skip if user did not ask to run the stack).

### D. Optional (does not block COMPLETE)

- [ ] **D1** Calibration dashboard has data (needs real settled markets — cannot be faked without fixtures).
- [ ] **D2** CME FedWatch side-by-side fetch succeeds (network-dependent).
- [ ] **D3** Public deploy (Fly.io / Railway / Vercel) — **deferred by user**; local demo + screenshot (F4) is enough for CV.

### E. Resume polish & statistical depth (blocks COMPLETE until done)

Quant SWE portfolio deliverables. See [`docs/resume-packaging.md`](docs/resume-packaging.md),
[`docs/research/fed-calibration-report.md`](docs/research/fed-calibration-report.md),
[`TASKS.md`](TASKS.md) Phase 11.

- [x] **E1** Landing home page at `/` — feature overview, pipeline diagram, links to modules (not redirect-only).
- [x] **E2** `docs/research/fed-calibration-report.md` filled with real numbers: Brier, log loss, Murphy decomposition, ECE (≥5 resolved `fed` markets).
- [x] **E3** Settled-market data for calibration: `scripts/backfill-settled-markets.sh` **or** ≥5 rows with `markets.status = 'settled'` and known outcomes.
- [x] **E4** `docs/resume-packaging.md` resume bullets filled (no `TBD` in bullets 1–3).
- [x] **E5** **ECE** (expected calibration error, 10-bin) in `analytics/calibration.py`, exposed via API + shown on `/calibration`.
- [x] **E6** Rolling calibration drift (e.g. 30d rolling Brier/ECE) on `/calibration` chart.
- [x] **E7** Arb aggregate stats: API field + UI card — violations/day, median severity bps, optional half-life estimate.
- [x] **E8** `docs/post-mortem.md` performance table filled + ≥3 documented incidents/fixes.
- [x] **E9** README `## Demo` section: local URL, optional deploy URL, screenshot path.
- [x] **E10** `meridian experiment export <id> --format json|md` for reproducible research artifacts.
- [x] **E11** FedWatch panel: Kalshi implied PMF vs CME FedWatch strip (or documented graceful degrade + fixture mode).
- [x] **E12** Unit tests for new E5/E7 helpers (≥8 tests combined); `make test` still passes.

### F. Admissions & portfolio packaging — Phase 12 (blocks COMPLETE until done)

Target: **quant SWE / ML / MFE** applications. Full context:
[`docs/admissions-roadmap.md`](docs/admissions-roadmap.md) · [`docs/resume-packaging.md`](docs/resume-packaging.md)

**Rules:** One F item per agent session unless user says otherwise. Do not add trading
execution. Label synthetic vs live data clearly in all research docs.

#### F — Ops & visibility

- [x] **F4** Screenshot committed: `docs/images/terminal-home.png`; referenced in README Demo (local URL is fine — no public deploy required).

#### F — Credibility (empirical)

- [x] **F1** Real settled-market calibration: ≥20 **actually settled** Kalshi markets (REST or script `scripts/backfill-real-settled-markets.sh`); add **"Live settled data"** section to `docs/research/fed-calibration-report.md` with Brier/ECE/Murphy on real outcomes only.
- [x] **F2** Event study memo: `docs/research/fomc-event-study.md` — ≥3 FOMC/CPI events, Δp_mid pre/post windows, bootstrap 95% CIs, interpretation.
- [x] **F8** CME FedWatch: live side-by-side on `/fedwatch` **or** fixture mode documented in `docs/fedwatch.md` with clear "live vs fixture" label.

#### F — Differentiation (complete exactly ONE)

- [ ] **F7a** Cross-venue memo: `docs/research/cross-venue-efficiency.md` (Kalshi ↔ Polymarket links, divergence, half-life).
- [x] **F7b** Microstructure memo: `docs/research/microstructure-memo.md` (Kyle λ, Amihud, regime-conditional spread).
- [ ] **F7c** Forecast eval: Diebold–Mariano test (microprice vs p_mid vs isotonic) in code + `docs/research/forecast-comparison.md`.

Check only **one** of F7a / F7b / F7c.

#### F — Packaging

- [x] **F5** Two-page brief: `docs/meridian-brief.md` (problem, architecture, key results, demo link).
- [x] **F6** Jupyter walkthrough: `notebooks/fed_calibration_walkthrough.ipynb`.
- [x] **F9** `docs/resume-packaging.md` complete: SOP paragraph filled, links checklist all checked, CV bullets cite **live** calibration numbers where available.
- [ ] **F10** `make test` passes; ≥6 unit tests if F7c adds forecast-eval code.

---

## How verification works

`scripts/check-completion.sh` tests sections **A–C**, **E**, and **F** automatically and:

1. Prints each gate as `OK:` or `FAIL:` with a one-line reason.
2. On full pass: rewrites the status block above to `STATUS: COMPLETE` + ISO timestamp.
3. Exits 0 only when A1–A4, B1–B3, C1–C6, E1–E12, and F1–F2, F4–F10 (with exactly one F7) all pass.

**Note:** Section **D** (including **D3 public deploy**) is optional and never blocks COMPLETE.

Run manually after changes:

```sh
bash scripts/check-completion.sh          # full check (needs stack for C gates)
bash scripts/check-completion.sh --code   # A + E + F file gates (no Docker)
```

---

## Session handoff template

When ending an incomplete session, append a one-line note under **Last session**:

```
Last session: YYYY-MM-DD — completed F2 (event study); F3 deploy still open.
```

---

Last session: 2026-06-19 — A–E complete; Phase 12 (section F) opened for admissions packaging.
Last session: 2026-06-21 — User deferred public deploy (D3); finish F4, F1, F8, one F7, F5/F6/F9 without F3.
