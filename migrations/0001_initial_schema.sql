-- 0001_initial_schema
-- Phase 1 baseline schema: venues, markets, market_groups, news_events (OLTP)
-- plus ticks, book_snapshots, signals (Timescale hypertables).
--
-- Assumes the timescaledb extension was already enabled by
-- docker/timescale/init.sql at first boot of the Postgres data volume.

BEGIN;

-- ─── OLTP tables ────────────────────────────────────────────────────────────

CREATE TABLE venues (
    id    SMALLSERIAL PRIMARY KEY,
    code  TEXT NOT NULL UNIQUE,
    name  TEXT NOT NULL
);

INSERT INTO venues (code, name) VALUES
    ('kalshi',     'Kalshi'),
    ('polymarket', 'Polymarket');

CREATE TABLE market_groups (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    label       TEXT NOT NULL,
    group_type  TEXT NOT NULL CHECK (group_type IN ('partition', 'implication', 'cross_venue')),
    description TEXT,
    metadata    JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE markets (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    venue_id          SMALLINT NOT NULL REFERENCES venues(id),
    external_id       TEXT NOT NULL,
    ticker            TEXT,
    question          TEXT NOT NULL,
    category          TEXT NOT NULL,
    market_group_id   UUID REFERENCES market_groups(id),
    opens_at          TIMESTAMPTZ,
    closes_at         TIMESTAMPTZ,
    settled_at        TIMESTAMPTZ,
    resolution_status TEXT NOT NULL DEFAULT 'open'
        CHECK (resolution_status IN ('open', 'closed', 'settled', 'voided')),
    settled_value     SMALLINT CHECK (settled_value IN (0, 1)),
    metadata          JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (venue_id, external_id)
);

CREATE INDEX markets_category_idx ON markets(category);
CREATE INDEX markets_group_idx    ON markets(market_group_id) WHERE market_group_id IS NOT NULL;
CREATE INDEX markets_open_idx     ON markets(closes_at)       WHERE resolution_status = 'open';

CREATE TABLE news_events (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    occurred_at TIMESTAMPTZ NOT NULL,
    category    TEXT NOT NULL,
    label       TEXT NOT NULL,
    source      TEXT,
    metadata    JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX news_events_time_idx ON news_events(occurred_at);

-- ─── Time-series hypertables ────────────────────────────────────────────────

CREATE TABLE ticks (
    event_ts     TIMESTAMPTZ NOT NULL,
    market_id    UUID        NOT NULL REFERENCES markets(id),
    sequence_no  BIGINT      NOT NULL,
    kind         TEXT        NOT NULL CHECK (kind IN ('quote', 'trade', 'status')),
    bid          NUMERIC(5,4) CHECK (bid          IS NULL OR (bid          >= 0 AND bid          <= 1)),
    ask          NUMERIC(5,4) CHECK (ask          IS NULL OR (ask          >= 0 AND ask          <= 1)),
    bid_size     INTEGER      CHECK (bid_size     IS NULL OR bid_size      >= 0),
    ask_size     INTEGER      CHECK (ask_size     IS NULL OR ask_size      >= 0),
    trade_price  NUMERIC(5,4) CHECK (trade_price  IS NULL OR (trade_price  >= 0 AND trade_price  <= 1)),
    trade_size   INTEGER      CHECK (trade_size   IS NULL OR trade_size    >= 1),
    aggressor    TEXT         CHECK (aggressor    IS NULL OR aggressor IN ('buy', 'sell')),
    payload      JSONB        NOT NULL DEFAULT '{}'::jsonb,
    ingest_ts    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (market_id, sequence_no, event_ts)
);

SELECT create_hypertable('ticks', 'event_ts', chunk_time_interval => INTERVAL '1 day');
CREATE INDEX ticks_market_time_idx ON ticks(market_id, event_ts DESC);

CREATE TABLE book_snapshots (
    event_ts     TIMESTAMPTZ NOT NULL,
    market_id    UUID        NOT NULL REFERENCES markets(id),
    sequence_no  BIGINT      NOT NULL,
    side         TEXT        NOT NULL CHECK (side IN ('bid', 'ask')),
    level        SMALLINT    NOT NULL CHECK (level >= 0),
    price        NUMERIC(5,4) NOT NULL CHECK (price >= 0 AND price <= 1),
    size         INTEGER      NOT NULL CHECK (size  >= 0),
    ingest_ts    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (market_id, sequence_no, side, level, event_ts)
);

SELECT create_hypertable('book_snapshots', 'event_ts', chunk_time_interval => INTERVAL '1 day');

CREATE TABLE signals (
    event_ts     TIMESTAMPTZ NOT NULL,
    market_id    UUID        REFERENCES markets(id),
    signal_type  TEXT        NOT NULL,
    value        DOUBLE PRECISION,
    metadata     JSONB       NOT NULL DEFAULT '{}'::jsonb,
    ingest_ts    TIMESTAMPTZ NOT NULL DEFAULT now()
);

SELECT create_hypertable('signals', 'event_ts', chunk_time_interval => INTERVAL '1 day');
CREATE INDEX signals_type_time_idx   ON signals(signal_type, event_ts DESC);
CREATE INDEX signals_market_time_idx ON signals(market_id,   event_ts DESC)
    WHERE market_id IS NOT NULL;

COMMIT;
