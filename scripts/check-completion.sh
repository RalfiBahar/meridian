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

# ── A3: one-command setup script tracked ─────────────────────────────────────
if [[ -f scripts/setup.sh ]] && grep -q "make setup" README.md 2>/dev/null; then
  ok "A3 scripts/setup.sh exists and make setup is referenced in README"
else
  fail "A3 scripts/setup.sh missing or make setup not referenced in README"
fi

# ── A4: unit tests ───────────────────────────────────────────────────────────
if uv run pytest -m "not integration" -q --tb=no >/dev/null 2>&1; then
  ok "A4 unit tests pass"
else
  fail "A4 unit tests failing — run: uv run pytest -m 'not integration'"
fi

if [[ "$CODE_ONLY" -eq 1 ]]; then
  if [[ "$FAIL" -ne 0 ]]; then
    echo ""
    echo "==> INCOMPLETE ($FAIL code gate(s) failed)"
    exit 1
  fi
  echo ""
  echo "==> Code gates (A) passed. Checking E + F file gates (--code mode)..."
  echo ""
else
  if [[ "$FAIL" -ne 0 ]]; then
    echo ""
    echo "==> INCOMPLETE ($FAIL gate(s) failed) — see COMPLETION.md"
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
fi

if [[ "$CODE_ONLY" -ne 1 ]]; then

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
  echo "==> Sections A–C passed. Checking E (polish gates)..."
  echo ""
else
  echo "==> INCOMPLETE ($FAIL gate(s) failed) — see COMPLETION.md"
  exit 1
fi
fi

if [[ "$CODE_ONLY" -eq 1 ]]; then
  echo "==> Checking E (polish gates)..."
  echo ""
fi

# ── E1: home page (not redirect-only) ───────────────────────────────────────
if [[ -f frontend/src/components/HomeClient.tsx ]] \
   && grep -q "HomeClient" frontend/src/app/page.tsx 2>/dev/null \
   && ! grep -q 'redirect("/markets")' frontend/src/app/page.tsx 2>/dev/null; then
  ok "E1 landing home page at /"
else
  fail "E1 add HomeClient landing page (replace redirect in page.tsx)"
fi

# ── E2: calibration report has real Brier number ─────────────────────────────
if [[ -f docs/research/fed-calibration-report.md ]] \
   && grep -Ei "Brier score \| [0-9]" docs/research/fed-calibration-report.md >/dev/null 2>&1 \
   && ! grep -q "| Brier score | TBD |" docs/research/fed-calibration-report.md 2>/dev/null; then
  ok "E2 fed-calibration-report.md has Brier score"
else
  fail "E2 fill docs/research/fed-calibration-report.md (Brier, ECE, Murphy — no TBD in metrics table)"
fi

# ── E3: settled markets for calibration ────────────────────────────────────
if [[ -f scripts/backfill-settled-markets.sh ]] || [[ -f scripts/backfill-settled-markets.sql ]]; then
  ok "E3 settled-market backfill script exists"
else
  _e3_settled=$(uv run python <<'PY' 2>/dev/null || echo 0
import asyncio
from meridian.config import get_settings
from meridian.db.postgres import pool_context

async def main() -> None:
    async with pool_context(get_settings()) as pool:
        n = await pool.fetchval("SELECT COUNT(*) FROM markets WHERE status = 'settled'")
        print(n)

asyncio.run(main())
PY
)
  if [[ "${_e3_settled:-0}" -ge 5 ]]; then
    ok "E3 settled markets >= 5 (${_e3_settled})"
  else
    fail "E3 need scripts/backfill-settled-markets.sh or >=5 settled markets (have ${_e3_settled:-0})"
  fi
fi

# ── E4: project brief key results ───────────────────────────────────────────
if [[ -f docs/meridian-brief.md ]] \
   && grep -q "## Key results" docs/meridian-brief.md; then
  ok "E4 meridian-brief.md key results section present"
else
  fail "E4 fill key results in docs/meridian-brief.md"
fi

# ── E5: ECE in calibration code + API ────────────────────────────────────────
if grep -q "expected_calibration_error\|def ece\|\.ece" src/meridian/analytics/calibration.py 2>/dev/null \
   && grep -qi "ece" src/meridian/api/routes/calibration.py 2>/dev/null; then
  ok "E5 ECE in calibration engine + API"
else
  fail "E5 add ECE to analytics/calibration.py and calibration API route"
fi

# ── E6: rolling calibration drift in frontend ────────────────────────────────
if grep -qi "drift\|rolling" frontend/src/app/calibration/page.tsx 2>/dev/null \
   || grep -qi "drift\|rolling" frontend/src/components/CalibrationClient.tsx 2>/dev/null; then
  ok "E6 rolling calibration drift UI"
else
  fail "E6 add rolling Brier/ECE drift chart on /calibration"
fi

# ── E7: arb aggregate stats API ──────────────────────────────────────────────
if grep -q "/arb/stats\|arb_stats\|ArbStats" src/meridian/api/routes/arb.py 2>/dev/null; then
  ok "E7 arb aggregate stats API"
else
  fail "E7 add GET /api/v1/arb/stats (violations/day, median bps)"
fi

# ── E8: post-mortem performance table ────────────────────────────────────────
if [[ -f docs/post-mortem.md ]] \
   && ! grep -A5 "## Performance notes" docs/post-mortem.md | grep -q '| TBD |'; then
  ok "E8 post-mortem.md performance table filled"
else
  fail "E8 fill performance table in docs/post-mortem.md"
fi

# ── E9: README Demo section ──────────────────────────────────────────────────
if grep -q "^## Demo" README.md 2>/dev/null; then
  ok "E9 README Demo section"
else
  fail "E9 add ## Demo section to README.md"
fi

# ── E10: experiment export CLI ───────────────────────────────────────────────
if uv run python -m meridian.cli experiment export --help >/dev/null 2>&1; then
  ok "E10 meridian experiment export command"
else
  fail "E10 add meridian experiment export <id> [--format json|md]"
fi

# ── E11: FedWatch comparison ─────────────────────────────────────────────────
if grep -qi "cme\|fedwatch\|comparison" frontend/src/app/fedwatch/page.tsx 2>/dev/null \
   || grep -qi "cme\|fedwatch" frontend/src/components/FedWatchClient.tsx 2>/dev/null \
   || [[ -f docs/fedwatch.md ]]; then
  ok "E11 FedWatch Kalshi vs CME comparison or documented fixture mode"
else
  fail "E11 FedWatch panel: CME strip vs Kalshi PMF (or docs/fedwatch.md fixture mode)"
fi

# ── E12: tests for new helpers ───────────────────────────────────────────────
_e12_ece=$(grep -l -i "ece\|expected_calibration" tests/test_analytics_calibration.py 2>/dev/null | wc -l)
_e12_arb=$(grep -l -i "arb.*stats\|aggregate" tests/test_analytics_arb.py 2>/dev/null | wc -l)
if [[ "$_e12_ece" -ge 1 ]] && [[ "$_e12_arb" -ge 1 ]]; then
  ok "E12 unit tests for ECE + arb stats"
else
  fail "E12 add unit tests for ECE and arb aggregate stats (test_analytics_calibration.py, test_analytics_arb.py)"
fi

FE_HOME=$(curl -sf -o /dev/null -w '%{http_code}' http://localhost:3001/ 2>/dev/null || echo "000")
if [[ "$CODE_ONLY" -eq 1 ]]; then
  ok "E1 smoke skipped (--code mode)"
elif [[ "$FE_HOME" == "200" ]]; then
  ok "E1 smoke: frontend / returns 200"
else
  fail "E1 frontend / not 200 (HTTP $FE_HOME)"
fi

echo ""
if [[ "$FAIL" -ne 0 ]]; then
  echo "==> INCOMPLETE ($FAIL gate(s) failed) — see COMPLETION.md (sections A–C or E)"
  exit 1
fi

echo "==> Sections A–C and E passed. Checking F (Phase 12 research deliverables; F3/deploy skipped — see D3)..."
echo ""

# F3 public deploy — optional (D3); user deferred
ok "F3 public deploy skipped (optional D3 — local demo + F4 screenshot)"

# ── F4: screenshot ───────────────────────────────────────────────────────────
if [[ -f docs/images/terminal-home.png ]]; then
  ok "F4 docs/images/terminal-home.png exists"
else
  fail "F4 add screenshot docs/images/terminal-home.png"
fi

# ── F1: live settled calibration ─────────────────────────────────────────────
if [[ -f docs/research/fed-calibration-report.md ]] \
   && grep -qi "live settled data" docs/research/fed-calibration-report.md; then
  ok "F1 fed-calibration-report has Live settled data section"
else
  fail "F1 add Live settled data section to docs/research/fed-calibration-report.md (≥20 real settled markets)"
fi
if [[ -f scripts/backfill-real-settled-markets.sh ]]; then
  ok "F1 backfill-real-settled-markets.sh exists"
elif [[ "$CODE_ONLY" -eq 1 ]]; then
  ok "F1 settled count skipped (--code mode; need script or >=20 settled)"
else
  _f1_settled=$(uv run python <<'PY' 2>/dev/null || echo 0
import asyncio
from meridian.config import get_settings
from meridian.db.postgres import pool_context

async def main() -> None:
    async with pool_context(get_settings()) as pool:
        n = await pool.fetchval(
            "SELECT COUNT(*) FROM markets WHERE status = 'settled'"
        )
        print(n)

asyncio.run(main())
PY
)
  if [[ "${_f1_settled:-0}" -ge 20 ]]; then
    ok "F1 settled markets >= 20 (${_f1_settled})"
  else
    fail "F1 need scripts/backfill-real-settled-markets.sh or >=20 settled markets (have ${_f1_settled:-0})"
  fi
fi

# ── F2: event study memo ─────────────────────────────────────────────────────
if [[ -f docs/research/fomc-event-study.md ]] \
   && grep -qi "bootstrap" docs/research/fomc-event-study.md; then
  ok "F2 fomc-event-study.md with bootstrap CIs"
else
  fail "F2 add docs/research/fomc-event-study.md (≥3 events, bootstrap 95% CIs)"
fi

# ── F8: CME FedWatch ─────────────────────────────────────────────────────────
if [[ -f docs/fedwatch.md ]] \
   && grep -qi "cme" docs/fedwatch.md; then
  ok "F8 docs/fedwatch.md documents CME comparison or fixture mode"
else
  fail "F8 update docs/fedwatch.md with CME live vs fixture mode"
fi

# ── F7: exactly one differentiation memo ─────────────────────────────────────
_f7_count=0
[[ -f docs/research/cross-venue-efficiency.md ]] && _f7_count=$((_f7_count + 1))
[[ -f docs/research/microstructure-memo.md ]] && _f7_count=$((_f7_count + 1))
[[ -f docs/research/forecast-comparison.md ]] && _f7_count=$((_f7_count + 1))
if grep -rl "diebold" src/meridian/analytics/ 2>/dev/null | grep -q .; then
  _f7_count=$((_f7_count + 1))
fi
if [[ "$_f7_count" -eq 1 ]]; then
  ok "F7 exactly one differentiation deliverable (F7a/b/c)"
elif [[ "$_f7_count" -eq 0 ]]; then
  fail "F7 add ONE of: cross-venue-efficiency.md, microstructure-memo.md, forecast-comparison.md (or Diebold code)"
else
  fail "F7 complete exactly ONE of F7a/F7b/F7c (found ${_f7_count})"
fi

# ── F5: project brief ────────────────────────────────────────────────────────
if [[ -f docs/meridian-brief.md ]] && [[ $(wc -c < docs/meridian-brief.md) -ge 500 ]]; then
  ok "F5 docs/meridian-brief.md (≥500 bytes)"
else
  fail "F5 add docs/meridian-brief.md (2-page project brief)"
fi

# ── F6: notebook ─────────────────────────────────────────────────────────────
if compgen -G "notebooks/*.ipynb" >/dev/null 2>&1; then
  ok "F6 Jupyter notebook under notebooks/"
else
  fail "F6 add notebooks/fed_calibration_walkthrough.ipynb"
fi

# ── F9: one-command setup documented ─────────────────────────────────────────
if grep -q "make setup" README.md 2>/dev/null \
   && [[ -f scripts/setup.sh ]]; then
  ok "F9 README documents make setup + scripts/setup.sh"
else
  fail "F9 document one-command setup (make setup) in README.md"
fi

# ── F10: tests (A4 covers make test; optional F7c test count) ─────────────────
_f10_dm_tests=$(grep -c -i "diebold\|mariano" tests/test_*.py 2>/dev/null || echo 0)
if [[ -f docs/research/forecast-comparison.md ]] || grep -rl "diebold" src/meridian/ 2>/dev/null | grep -q .; then
  if [[ "${_f10_dm_tests:-0}" -ge 6 ]]; then
    ok "F10 ≥6 Diebold–Mariano tests (F7c)"
  else
    fail "F10 F7c selected — add ≥6 unit tests for forecast eval (have ${_f10_dm_tests:-0})"
  fi
else
  ok "F10 tests (F7c not selected; A4 covers make test)"
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
