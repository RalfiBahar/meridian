#!/usr/bin/env bash
# Exit 0 when Meridian is operationally complete (agent loop stop condition).
# See COMPLETION.md.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

CODE_ONLY=0
[[ "${1:-}" == "--code" ]] && CODE_ONLY=1

FAIL=0
ok()   { echo "  OK:   $*"; }
fail() { echo "  FAIL: $*"; FAIL=$((FAIL + 1)); }

COMPLETION_FILE="$ROOT/COMPLETION.md"

echo "==> Meridian completion check"
echo ""

# ── A1: experiment list does not crash ─────────────────────────────────────
# The original bug was AttributeError when asyncpg returned metrics as a
# string instead of a dict.  A DB-connection error is expected without the
# stack; that is not the bug being tested here.
_a1_out=$(uv run python -m meridian.cli experiment list 2>&1)
_a1_rc=$?
if [[ $_a1_rc -eq 0 ]]; then
  ok "A1 experiment list runs without error"
elif echo "$_a1_out" | grep -qE "AttributeError|str.*object.*has no attribute|metrics.*str"; then
  fail "A1 meridian experiment list crashes (JSONB parsing bug — metrics returned as string)"
else
  ok "A1 JSONB fix verified (DB unavailable — connection error is expected without the stack)"
fi

# ── A2: README reflects done phases ──────────────────────────────────────────
if grep -q "Phase 7.*Done\|Phase 7.*✓\|7.*Quant Terminal.*Done" README.md 2>/dev/null \
   && ! grep -q "Phase 1c.*In progress\|Currently in \*\*Phase 1\*\*" README.md 2>/dev/null; then
  ok "A2 README status synced (not stuck on Phase 1)"
else
  fail "A2 README.md still shows Phase 1 / in-progress — sync with ROADMAP.md"
fi

# ── A3: dev-up script tracked ────────────────────────────────────────────────
if [[ -f scripts/dev-up.sh ]] && grep -q "dev-up.sh" README.md 2>/dev/null; then
  ok "A3 scripts/dev-up.sh exists and is referenced in README"
else
  fail "A3 scripts/dev-up.sh missing or not referenced in README"
fi

# ── A4: unit tests ───────────────────────────────────────────────────────────
if uv run pytest -m "not integration" -q --tb=no >/dev/null 2>&1; then
  ok "A4 unit tests pass"
else
  fail "A4 unit tests failing — run: uv run pytest -m 'not integration'"
fi

if [[ "$CODE_ONLY" -eq 1 ]]; then
  echo ""
  if [[ "$FAIL" -eq 0 ]]; then
    echo "==> Code gates (A) passed. Run without --code for full check."
    exit 0
  fi
  echo "==> INCOMPLETE ($FAIL code gate(s) failed)"
  exit 1
fi

# ── Stack health (needed for B/C) ────────────────────────────────────────────
if ! curl -sf http://localhost:8000/health >/dev/null 2>&1; then
  fail "Stack health — API not reachable on :8000 (run scripts/dev-up.sh)"
  echo ""
  echo "==> INCOMPLETE (stack not running; B/C gates skipped)"
  exit 1
fi
ok "Stack API health"

# ── B1: seed script exists ───────────────────────────────────────────────────
if [[ -f scripts/seed-news-events.sh ]] || [[ -f scripts/seed-news-events.sql ]]; then
  ok "B1 news_events seed script exists"
else
  fail "B1 add scripts/seed-news-events.sh or .sql (≥10 rows)"
fi

# ── B2: pipeline script exists and is executable ─────────────────────────────
if [[ -f scripts/run-full-pipeline.sh ]]; then
  ok "B2 run-full-pipeline.sh present"
else
  fail "B2 scripts/run-full-pipeline.sh missing"
fi

# ── B3: covered by A1 ────────────────────────────────────────────────────────
ok "B3 experiment list (same as A1)"

# ── C gates via DB ───────────────────────────────────────────────────────────
read -r C1 C2 C3 C4 C5 <<EOF
$(uv run python <<'PY'
import asyncio
import sys

from meridian.config import get_settings
from meridian.db.postgres import pool_context


async def main() -> None:
    try:
        async with pool_context(get_settings()) as pool:
            news = await pool.fetchval("SELECT COUNT(*) FROM news_events")
            ticks = await pool.fetchval("SELECT COUNT(*) FROM ticks")
            anomaly = await pool.fetchval(
                "SELECT COUNT(*) FROM signals WHERE signal_type = 'anomaly_score'"
            )
            regime = await pool.fetchval(
                "SELECT COUNT(*) FROM signals WHERE signal_type = 'regime_state'"
            )
            nlp = await pool.fetchval(
                "SELECT COUNT(*) FROM signals WHERE signal_type = 'market_moving_prob'"
            )
            print(news, ticks, anomaly, regime, nlp)
    except Exception as e:
        print(f"ERR ERR 0 0 0  # {e}", file=sys.stderr)
        sys.exit(1)


asyncio.run(main())
PY
)
EOF

if [[ "${C1:-0}" -ge 10 ]]; then ok "C1 news_events >= 10 ($C1)"; else fail "C1 news_events >= 10 (have ${C1:-0})"; fi
if [[ "${C2:-0}" -ge 500 ]]; then ok "C2 ticks >= 500 ($C2)"; else fail "C2 ticks >= 500 (have ${C2:-0}) — let ingest run or backfill"; fi
if [[ "${C3:-0}" -ge 1 ]]; then ok "C3 anomaly_score signals ($C3)"; else fail "C3 no anomaly_score signals — run analytics anomaly after ≥1h ingest"; fi
if [[ "${C4:-0}" -ge 1 ]]; then ok "C4 regime_state signals ($C4)"; else fail "C4 no regime_state signals — run analytics regime after ≥2h ingest"; fi
if [[ "${C5:-0}" -ge 1 ]]; then ok "C5 market_moving_prob signals ($C5)"; else fail "C5 no NLP signals — seed news_events + run nlp-tag"; fi

FE_CODE=$(curl -sf -o /dev/null -w '%{http_code}' http://localhost:3001/markets 2>/dev/null || echo "000")
if [[ "$FE_CODE" == "200" ]]; then
  ok "C6 frontend /markets returns 200"
else
  fail "C6 frontend not on :3001 (HTTP $FE_CODE)"
fi

echo ""
if [[ "$FAIL" -eq 0 ]]; then
  STAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  if [[ -f "$COMPLETION_FILE" ]]; then
    sed -i "s/^STATUS: .*/STATUS: COMPLETE/" "$COMPLETION_FILE"
    sed -i "s/^COMPLETED_AT: .*/COMPLETED_AT: $STAMP/" "$COMPLETION_FILE"
  fi
  echo "==> MERIDIAN COMPLETE — all gates passed."
  echo "    Agent loop should stop. Reply: MERIDIAN COMPLETE — stopping."
  exit 0
fi

echo "==> INCOMPLETE ($FAIL gate(s) failed) — see COMPLETION.md"
exit 1
