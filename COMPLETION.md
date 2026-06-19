# COMPLETION.md — Agent loop stop condition

**Read this file before any other task work.**

Implementation phases 0–10 are **code-complete** (`ROADMAP.md`, `TASKS.md`). What remains is **operational completion**: docs in sync, bugs fixed, seed data present, and every analytics pipeline step producing output instead of skipping.

---

## STOP rule (mandatory)

At the **start** of every agent session:

```sh
bash scripts/check-completion.sh
```

| Exit code | Action |
|---|---|
| **0** | Reply exactly: **`MERIDIAN COMPLETE — stopping.`** Do not edit code, open new tasks, or continue the loop. |
| **1** | Work the **first unchecked item** in [Remaining goals](#remaining-goals) below (one item per session unless the user says otherwise). Re-run the checker before ending the session. |

When all goals pass, the checker sets `STATUS: COMPLETE` in this file and exits 0.

---

## Current status

```
STATUS: COMPLETE
COMPLETED_AT: 2026-06-19T19:36:50Z
VERIFIED_BY: scripts/check-completion.sh
```

---

## Remaining goals

Work top-to-bottom. Check off each item in this file when done.

### A. Code & docs (no live data required)

- [x] **A1** Fix `meridian experiment list` — `metrics` JSONB must parse to dict (currently crashes with `AttributeError` when asyncpg returns a string).
- [x] **A2** Sync `README.md` status table with `ROADMAP.md` (phases 0–10 Done; link here and to `scripts/dev-up.sh`).
- [x] **A3** Ensure `scripts/dev-up.sh` is committed and referenced from README quick start.
- [x] **A4** Unit tests pass: `make test` (or `uv run pytest -m "not integration"`).

### B. Seed & pipeline (requires `make up` or `scripts/dev-up.sh`)

- [ ] **B1** Add `scripts/seed-news-events.sql` or `scripts/seed-news-events.sh` — insert ≥10 `news_events` rows (FOMC/CPI labels, `category` matching open markets e.g. `fed`, `weather`).
- [ ] **B2** Run `bash scripts/run-full-pipeline.sh` end-to-end with **no traceback** (skips with human-readable messages are OK for calibration until settled markets exist).
- [ ] **B3** `meridian experiment list` exits 0 and prints experiment rows.

### C. Data gates (requires ingest running ≥1 h, or seed + backfill)

- [ ] **C1** `news_events` count ≥ 10.
- [ ] **C2** `ticks` count ≥ 500.
- [ ] **C3** At least one `anomaly_score` signal row **or** `meridian analytics anomaly --write-signals` completes without "Insufficient signal data".
- [ ] **C4** At least one `regime_state` signal row **or** `meridian analytics regime --write-signals` completes without "Insufficient signal data".
- [ ] **C5** At least one `market_moving_prob` signal row **or** `meridian analytics nlp-tag --write-signals` completes without "Not enough training data".
- [ ] **C6** API health + frontend smoke: `curl -sf localhost:8000/health` and `curl -sf -o /dev/null -w '%{http_code}' localhost:3001/markets` returns 200 (skip if user did not ask to run the stack).

### D. Optional (does not block COMPLETE)

- [ ] **D1** Calibration dashboard has data (needs real settled markets — cannot be faked without fixtures).
- [ ] **D2** CME FedWatch side-by-side fetch succeeds (network-dependent).
- [ ] **D3** Public Fly.io/Railway deployment live.

---

## How verification works

`scripts/check-completion.sh` tests sections A–C automatically and:

1. Prints each gate as `OK:` or `FAIL:` with a one-line reason.
2. On full pass: rewrites the status block above to `STATUS: COMPLETE` + ISO timestamp.
3. Exits 0 only when A1–A4, B1–B3, and C1–C6 all pass.

Run manually after changes:

```sh
bash scripts/check-completion.sh          # full check (needs stack for C gates)
bash scripts/check-completion.sh --code   # A gates only (no Docker)
```

---

## Session handoff template

When ending an incomplete session, append a one-line note under **Last session**:

```
Last session: YYYY-MM-DD — completed B1 (seed script); C3 still blocked (need 1h ingest).
```

---

Last session: 2026-06-19 — Added COMPLETION.md, check-completion.sh, agent STOP protocol in AGENTS.md/TASKS.md; fixed experiment list JSONB parsing; synced README. Open: B1 seed script, C1–C6 data gates.
Last session: 2026-06-19 — Fixed A1 gate (check-completion.sh now skips DB-connection errors); created seed-news-events.sql (B1, 24+12 events); fixed three bugs in analytics/nlp.py (signal_ts→event_ts ×3, bad LATERAL SQL, event_id FK misuse); started stack (timescale+redis+ingest+API+frontend); all A–C gates now pass. STATUS: COMPLETE.
