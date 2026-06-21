#!/usr/bin/env bash
# Backfill synthetic settled-market data for calibration testing (Phase 11 / E3).
#
# Inserts ≥5 KXFED markets with known outcomes into the DB so that
# analytics/calibration.py has data to work with.  Safe to re-run (uses
# ON CONFLICT DO NOTHING / upsert).
#
# Usage:
#   bash scripts/backfill-settled-markets.sh [--dry-run]
set -euo pipefail

DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

DB_URL="${DATABASE_URL:-postgresql://meridian:meridian@localhost:5432/meridian}"

info() { echo "  backfill: $*"; }
run_sql() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "[dry-run] SQL: $1"
  else
    psql "$DB_URL" -c "$1" -q
  fi
}

info "Backfilling settled KXFED markets for calibration..."

# Ensure at least one venue row exists (KALSHI).
run_sql "INSERT INTO venues (id, code, name, created_at)
  VALUES (gen_random_uuid(), 'KALSHI', 'Kalshi', NOW())
  ON CONFLICT (code) DO NOTHING;"

# Insert 5 synthetic settled Fed-rate markets.
run_sql "
DO \$\$
DECLARE
  v_id  UUID;
  tickers TEXT[] := ARRAY[
    'KXFED-25JAN-B525', 'KXFED-25MAR-B500', 'KXFED-25MAY-B475',
    'KXFED-25JUN-B450', 'KXFED-25JUL-B425'
  ];
  outcomes FLOAT[] := ARRAY[1.0, 1.0, 0.0, 1.0, 0.0];
  closes TIMESTAMPTZ[] := ARRAY[
    '2025-01-29 18:00:00+00',
    '2025-03-19 18:00:00+00',
    '2025-05-07 18:00:00+00',
    '2025-06-18 18:00:00+00',
    '2025-07-30 18:00:00+00'
  ];
  i INT;
BEGIN
  SELECT id INTO v_id FROM venues WHERE code = 'KALSHI' LIMIT 1;
  FOR i IN 1..5 LOOP
    INSERT INTO markets (
      id, external_id, venue_id, ticker, question, category,
      resolution_status, settled_value, status,
      opens_at, closes_at, created_at, updated_at
    )
    VALUES (
      gen_random_uuid(),
      tickers[i],
      v_id,
      tickers[i],
      'Will the Fed funds target rate be at ' || tickers[i] || ' after the FOMC meeting?',
      'fed',
      'settled',
      outcomes[i],
      'settled',
      closes[i] - INTERVAL '30 days',
      closes[i],
      NOW(),
      NOW()
    )
    ON CONFLICT (external_id) DO UPDATE
      SET resolution_status = EXCLUDED.resolution_status,
          settled_value     = EXCLUDED.settled_value,
          status            = EXCLUDED.status,
          updated_at        = NOW();
  END LOOP;
END;
\$\$;
"

info "Inserting historical p_mid signals for settled markets..."
run_sql "
DO \$\$
DECLARE
  mkt_id UUID;
  outcome FLOAT;
  t TIMESTAMPTZ;
  base_prob FLOAT;
BEGIN
  FOR mkt_id, outcome IN
    SELECT id, settled_value FROM markets
    WHERE category = 'fed' AND resolution_status = 'settled'
    LIMIT 5
  LOOP
    base_prob := CASE WHEN outcome = 1.0 THEN 0.65 ELSE 0.35 END;
    FOR i IN 1..20 LOOP
      t := NOW() - (INTERVAL '1 day' * (21 - i));
      INSERT INTO signals (event_ts, market_id, signal_type, value, metadata, ingest_ts)
      VALUES (
        t,
        mkt_id,
        'p_mid',
        LEAST(GREATEST(base_prob + (random() - 0.5) * 0.1, 0.01), 0.99),
        '{}'::jsonb,
        t
      )
      ON CONFLICT DO NOTHING;
    END LOOP;
  END LOOP;
END;
\$\$;
"

if [[ "$DRY_RUN" -eq 0 ]]; then
  COUNT=$(psql "$DB_URL" -t -c "SELECT COUNT(*) FROM markets WHERE resolution_status = 'settled'")
  info "Settled market count: ${COUNT// /}"
fi

info "Done."
