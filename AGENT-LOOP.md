# Agent loop — Meridian

How to run autonomous agent sessions until Phase 12 (admissions packaging) is complete.

---

## Start the loop

From any shell (runs in foreground; use `tmux` for detach):

```sh
/srv/agent-platform/bin/agent-loop meridian-project \
  "Read COMPLETION.md and docs/admissions-roadmap.md first. Run bash scripts/check-completion.sh. Work the first unchecked goal in COMPLETION.md section F (Phase 12). One goal per session unless blocked. Re-run check-completion.sh before ending. If exit 0, reply exactly: MERIDIAN COMPLETE — stopping."
```

**Recommended — detached tmux session:**

```sh
tmux new-session -d -s agent-loop-meridian \
  "/srv/agent-platform/bin/agent-loop meridian-project \
  \"Read COMPLETION.md and docs/admissions-roadmap.md first. Run bash scripts/check-completion.sh. Work the first unchecked goal in COMPLETION.md section F (Phase 12). One goal per session unless blocked. Re-run check-completion.sh before ending. If exit 0, reply exactly: MERIDIAN COMPLETE — stopping.\""

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
bash scripts/dev-up.sh
```

---

## Read order for agents

1. `COMPLETION.md` — goals and STOP rule
2. `docs/admissions-roadmap.md` — Phase 12 context
3. `AGENTS.md` — invariants and layout
4. `TASKS.md` — Phase 12 task breakdown
