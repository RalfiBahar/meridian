# Agent loop — Meridian

How to run autonomous agent sessions until `check-completion.sh` exits 0.

---

## Start the loop

From any shell (runs in foreground; use `tmux` for detach):

```sh
/srv/agent-platform/bin/agent-loop meridian-project \
  "Read COMPLETION.md first. User deferred public deploy (D3) — do NOT work on Fly.io/Vercel deploy. Run bash scripts/check-completion.sh. If C gates fail, fix C first (seed news_events + run-full-pipeline.sh). Then work first unchecked F goal (F4, F1, F8, one F7, F5, F6, F9). Re-run check-completion.sh before ending. Reply MERIDIAN COMPLETE — stopping ONLY if check-completion.sh exit code is 0."
```

**Recommended — detached tmux session:**

```sh
tmux new-session -d -s agent-loop-meridian \
  '/srv/agent-platform/bin/agent-loop meridian-project \
  "Read COMPLETION.md first. User deferred public deploy (D3) — do NOT work on Fly.io/Vercel deploy. Run bash scripts/check-completion.sh. If C gates fail, fix C first (seed news_events + run-full-pipeline.sh). Then work first unchecked F goal (F4, F1, F8, one F7, F5, F6, F9). Re-run check-completion.sh before ending. Reply MERIDIAN COMPLETE — stopping ONLY if check-completion.sh exit code is 0."'

tmux attach -t agent-loop-meridian   # watch progress
# Ctrl-b d to detach
```

---

## Stop condition

The loop stops automatically when **either**:

1. `COMPLETION.md` contains `STATUS: COMPLETE` in the status block (set by `check-completion.sh`), **or**
2. The agent replies with **`MERIDIAN COMPLETE — stopping.`**

Do **not** start the loop while `STATUS: COMPLETE` — `agent-loop` exits immediately.

To reopen Phase 12 after completion, set `STATUS: INCOMPLETE` in `COMPLETION.md` and uncheck new goals.

---

## Monitor

| Path | Purpose |
|---|---|
| `/srv/projects/meridian-project/.agent/loop.log` | Loop runner log |
| `/srv/projects/meridian-project/.agent/logs/*.log` | Per-session Claude output |
| `/srv/projects/meridian-project/.agent/limit-state.json` | Rate-limit wait state |

```sh
tail -f /srv/projects/meridian-project/.agent/loop.log
```

---

## Manual verification

```sh
cd /srv/projects/meridian-project/repo
bash scripts/check-completion.sh          # full (needs stack for C gates)
bash scripts/check-completion.sh --code   # A + E + F file gates only
```

---

## Boot stack before data-heavy F items

F1/F2/F7 need DB + ingest:

```sh
make setup
# or: bash scripts/dev-up.sh
```

---

## Read order for agents

1. `COMPLETION.md` — goals and STOP rule
2. `docs/meridian-brief.md` — project summary and key results
3. `AGENTS.md` — invariants and layout
4. `TASKS.md` — phase task breakdown
