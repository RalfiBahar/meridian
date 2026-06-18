-- 0003_experiments
-- Phase 6: reproducible experiment tracker.
--
-- `experiments` records a run of a named experiment script with full provenance:
-- code SHA, parameters, data window, captured metrics, and free-text notes.
-- The (name, code_sha, params) unique constraint prevents accidental duplicate runs.

BEGIN;

CREATE TABLE experiments (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name         TEXT        NOT NULL,
    code_sha     TEXT        NOT NULL,    -- SHA-256 of experiments/<name>/run.py at run time
    params       JSONB       NOT NULL DEFAULT '{}'::jsonb,
    data_window  JSONB       NOT NULL DEFAULT '{}'::jsonb,  -- e.g. {"start":"2026-01-01","end":"2026-06-01"}
    metrics      JSONB       NOT NULL DEFAULT '{}'::jsonb,  -- output KPIs captured from run
    stdout       TEXT,                   -- captured stdout of the run script
    notes        TEXT,                   -- human annotations added after the run
    status       TEXT        NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    started_at   TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX experiments_name_idx       ON experiments(name, created_at DESC);
CREATE INDEX experiments_status_idx     ON experiments(status);

COMMIT;
