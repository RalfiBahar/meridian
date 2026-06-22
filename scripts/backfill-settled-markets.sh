#!/usr/bin/env bash
# Backfill SYNTHETIC settled-market data for offline/demo use only.
#
# Production calibration uses live Kalshi data via:
#   bash scripts/backfill-real-settled-markets.sh
#   uv run python -m meridian.cli markets sync-settled
#
# This script is kept as a last-resort fallback when the API is unavailable.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

DB_URL="${DATABASE_URL:-postgresql://meridian:meridian@localhost:5433/meridian}"
PSQL=(psql "$DB_URL" -v ON_ERROR_STOP=1)

info() { echo "  backfill: $*"; }

run_psql() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "[dry-run] would run SQL ($(wc -l <<< "$1") lines)"
    return 0
  fi
  if command -v psql >/dev/null 2>&1; then
    psql "$DB_URL" -v ON_ERROR_STOP=1 -q <<< "$1"
  elif docker ps --format '{{.Names}}' 2>/dev/null | grep -qx meridian-timescale; then
    docker exec -i meridian-timescale psql -U meridian -d meridian -v ON_ERROR_STOP=1 -q <<< "$1"
  else
    echo "backfill: no psql and meridian-timescale container not running" >&2
    exit 1
  fi
}

SQL=$(cat <<'EOSQL'
DO $$
DECLARE
  v_id SMALLINT := (SELECT id FROM venues WHERE code = 'kalshi' LIMIT 1);
  rec RECORD;
  mkt_id UUID;
  base_prob FLOAT;
  t TIMESTAMPTZ;
  j INT;
BEGIN
  IF v_id IS NULL THEN
    RAISE EXCEPTION 'kalshi venue row missing — run migrations first';
  END IF;

  FOR rec IN
    SELECT * FROM (VALUES
      ('fed',      'KXFED-25JAN-B525', 1, '2025-01-29 18:00:00+00'::timestamptz, 0.65),
      ('fed',      'KXFED-25MAR-B500', 1, '2025-03-19 18:00:00+00'::timestamptz, 0.65),
      ('fed',      'KXFED-25MAY-B475', 0, '2025-05-07 18:00:00+00'::timestamptz, 0.35),
      ('fed',      'KXFED-25JUN-B450', 1, '2025-06-18 18:00:00+00'::timestamptz, 0.65),
      ('fed',      'KXFED-25JUL-B425', 0, '2025-07-30 18:00:00+00'::timestamptz, 0.35),
      ('econ',     'KXCPI-25JAN-T3.0', 1, '2025-01-15 13:30:00+00'::timestamptz, 0.58),
      ('econ',     'KXCPI-25FEB-T2.8', 0, '2025-02-12 13:30:00+00'::timestamptz, 0.42),
      ('econ',     'KXCPI-25MAR-T3.1', 1, '2025-03-12 13:30:00+00'::timestamptz, 0.55),
      ('econ',     'KXUNRATE-25APR-4.0', 0, '2025-04-04 12:30:00+00'::timestamptz, 0.38),
      ('econ',     'KXGDP-25Q1-POS', 1, '2025-04-30 12:30:00+00'::timestamptz, 0.72),
      ('politics', 'KXPRES-24-NOV-D', 0, '2024-11-05 23:00:00+00'::timestamptz, 0.48),
      ('politics', 'KXPRES-24-NOV-R', 1, '2024-11-05 23:00:00+00'::timestamptz, 0.52),
      ('politics', 'KXSEN-24-PA-D',   1, '2024-11-05 23:00:00+00'::timestamptz, 0.61),
      ('politics', 'KXSEN-24-GA-R',   0, '2024-11-05 23:00:00+00'::timestamptz, 0.44),
      ('politics', 'KXHOUSE-24-CA13', 1, '2024-11-05 23:00:00+00'::timestamptz, 0.57),
      ('crypto',   'KXBTC-25JAN-100K', 0, '2025-01-31 23:59:00+00'::timestamptz, 0.28),
      ('crypto',   'KXBTC-25MAR-90K',  1, '2025-03-31 23:59:00+00'::timestamptz, 0.71),
      ('crypto',   'KXETH-25APR-4K',   0, '2025-04-30 23:59:00+00'::timestamptz, 0.33),
      ('crypto',   'KXBTC-25MAY-95K',  1, '2025-05-31 23:59:00+00'::timestamptz, 0.66),
      ('crypto',   'KXETH-25JUN-5K',   0, '2025-06-30 23:59:00+00'::timestamptz, 0.41),
      ('sports',   'KXNBA-25FINAL-LAL', 0, '2025-06-15 02:00:00+00'::timestamptz, 0.39),
      ('sports',   'KXNBA-25FINAL-BOS', 1, '2025-06-15 02:00:00+00'::timestamptz, 0.61),
      ('sports',   'KXMLB-25WS-NYY',    1, '2025-10-30 00:00:00+00'::timestamptz, 0.54),
      ('sports',   'KXNFL-25SB-KC',     0, '2025-02-09 23:30:00+00'::timestamptz, 0.47),
      ('sports',   'KXNFL-25SB-PHI',    1, '2025-02-09 23:30:00+00'::timestamptz, 0.53)
    ) AS t(cat, ticker, outcome, closes, anchor_prob)
  LOOP
    INSERT INTO markets (
      id, external_id, venue_id, ticker, question, category,
      resolution_status, settled_value,
      opens_at, closes_at, settled_at, created_at, updated_at
    )
    VALUES (
      gen_random_uuid(),
      rec.ticker,
      v_id,
      rec.ticker,
      '[synthetic settled] ' || rec.ticker,
      rec.cat,
      'settled',
      rec.outcome,
      rec.closes - INTERVAL '30 days',
      rec.closes,
      rec.closes,
      NOW(),
      NOW()
    )
    ON CONFLICT (venue_id, external_id) DO UPDATE
      SET resolution_status = EXCLUDED.resolution_status,
          settled_value     = EXCLUDED.settled_value,
          settled_at        = EXCLUDED.settled_at,
          category          = EXCLUDED.category,
          updated_at        = NOW()
    RETURNING id INTO mkt_id;

    -- Replace signals so re-runs stay idempotent.
    DELETE FROM signals
    WHERE market_id = mkt_id AND signal_type = 'p_mid';

    base_prob := rec.anchor_prob;
    FOR j IN 1..20 LOOP
      t := rec.closes - (INTERVAL '1 day' * (21 - j));
      INSERT INTO signals (event_ts, market_id, signal_type, value, metadata, ingest_ts)
      VALUES (
        t,
        mkt_id,
        'p_mid',
        LEAST(GREATEST(base_prob + (random() - 0.5) * 0.1, 0.01), 0.99),
        '{"source":"backfill-settled-markets"}'::jsonb,
        t
      );
    END LOOP;
  END LOOP;
END;
$$;
EOSQL
)

info "Backfilling settled markets for calibration (all categories)..."
run_psql "$SQL"

if [[ "$DRY_RUN" -eq 0 ]]; then
  SUMMARY=$(run_psql "SELECT category, COUNT(*) FROM markets WHERE resolution_status = 'settled' GROUP BY category ORDER BY category;" 2>/dev/null || true)
  info "Settled markets by category:"
  echo "$SUMMARY" | sed 's/^/    /'
fi

info "Done."
