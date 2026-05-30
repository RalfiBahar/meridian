-- 0002_book_delta_and_fractional_sizes
--
-- Phase 1c.2: prepare the schema for live Kalshi ingestion.
--
-- 1) Extend ticks.kind to include 'book_delta' (per-level signed updates
--    streamed from the orderbook_delta channel).
-- 2) Extend book_snapshots.side to allow Kalshi-native 'yes' / 'no' alongside
--    the universal 'bid' / 'ask'. We preserve raw venue semantics here and
--    let analytics canonicalize.
-- 3) Convert size columns from INTEGER to NUMERIC. Kalshi supports fractional
--    trading and reports sizes like "19.00" or "1500.50"; storing them as
--    INTEGER silently truncated.

BEGIN;

-- ─── ticks.kind ─────────────────────────────────────────────────────────────

ALTER TABLE ticks DROP CONSTRAINT IF EXISTS ticks_kind_check;
ALTER TABLE ticks ADD CONSTRAINT ticks_kind_check
    CHECK (kind IN ('quote', 'trade', 'status', 'book_delta'));

-- ─── ticks.size columns: INTEGER -> NUMERIC ─────────────────────────────────

ALTER TABLE ticks DROP CONSTRAINT IF EXISTS ticks_bid_size_check;
ALTER TABLE ticks DROP CONSTRAINT IF EXISTS ticks_ask_size_check;
ALTER TABLE ticks DROP CONSTRAINT IF EXISTS ticks_trade_size_check;

ALTER TABLE ticks
    ALTER COLUMN bid_size   TYPE NUMERIC USING bid_size::numeric,
    ALTER COLUMN ask_size   TYPE NUMERIC USING ask_size::numeric,
    ALTER COLUMN trade_size TYPE NUMERIC USING trade_size::numeric;

ALTER TABLE ticks ADD CONSTRAINT ticks_bid_size_check
    CHECK (bid_size   IS NULL OR bid_size   >= 0);
ALTER TABLE ticks ADD CONSTRAINT ticks_ask_size_check
    CHECK (ask_size   IS NULL OR ask_size   >= 0);
ALTER TABLE ticks ADD CONSTRAINT ticks_trade_size_check
    CHECK (trade_size IS NULL OR trade_size >  0);

-- ─── book_snapshots: relax side, switch size to NUMERIC ─────────────────────

ALTER TABLE book_snapshots DROP CONSTRAINT IF EXISTS book_snapshots_side_check;
ALTER TABLE book_snapshots ADD CONSTRAINT book_snapshots_side_check
    CHECK (side IN ('bid', 'ask', 'yes', 'no'));

ALTER TABLE book_snapshots DROP CONSTRAINT IF EXISTS book_snapshots_size_check;
ALTER TABLE book_snapshots
    ALTER COLUMN size TYPE NUMERIC USING size::numeric;
ALTER TABLE book_snapshots ADD CONSTRAINT book_snapshots_size_check
    CHECK (size >= 0);

COMMIT;
