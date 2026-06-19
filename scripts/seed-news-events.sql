-- Seed news_events rows for FOMC, CPI, and related macro events.
-- Includes historical events (for reference) and recent events (for NLP training).
-- Safe to run multiple times (ON CONFLICT DO NOTHING).

BEGIN;

-- Historical FOMC meetings (Fed rate decisions)
INSERT INTO news_events (occurred_at, category, label, source, metadata)
VALUES
  ('2024-01-31 19:00:00+00', 'fed', 'FOMC rate decision Jan 2024',  'Federal Reserve', '{"decision": "hold", "target_rate": 5.50}'),
  ('2024-03-20 18:00:00+00', 'fed', 'FOMC rate decision Mar 2024',  'Federal Reserve', '{"decision": "hold", "target_rate": 5.50}'),
  ('2024-05-01 18:00:00+00', 'fed', 'FOMC rate decision May 2024',  'Federal Reserve', '{"decision": "hold", "target_rate": 5.50}'),
  ('2024-06-12 18:00:00+00', 'fed', 'FOMC rate decision Jun 2024',  'Federal Reserve', '{"decision": "hold", "target_rate": 5.50}'),
  ('2024-07-31 18:00:00+00', 'fed', 'FOMC rate decision Jul 2024',  'Federal Reserve', '{"decision": "hold", "target_rate": 5.50}'),
  ('2024-09-18 18:00:00+00', 'fed', 'FOMC rate decision Sep 2024',  'Federal Reserve', '{"decision": "cut_50bps", "target_rate": 5.00}'),
  ('2024-11-07 19:00:00+00', 'fed', 'FOMC rate decision Nov 2024',  'Federal Reserve', '{"decision": "cut_25bps", "target_rate": 4.75}'),
  ('2024-12-18 19:00:00+00', 'fed', 'FOMC rate decision Dec 2024',  'Federal Reserve', '{"decision": "cut_25bps", "target_rate": 4.50}'),
  ('2025-01-29 19:00:00+00', 'fed', 'FOMC rate decision Jan 2025',  'Federal Reserve', '{"decision": "hold", "target_rate": 4.50}'),
  ('2025-03-19 18:00:00+00', 'fed', 'FOMC rate decision Mar 2025',  'Federal Reserve', '{"decision": "hold", "target_rate": 4.50}'),
  ('2025-05-07 18:00:00+00', 'fed', 'FOMC rate decision May 2025',  'Federal Reserve', '{"decision": "hold", "target_rate": 4.50}'),
  ('2025-06-18 18:00:00+00', 'fed', 'FOMC rate decision Jun 2025',  'Federal Reserve', '{"decision": "hold", "target_rate": 4.25}')
ON CONFLICT DO NOTHING;

-- Historical CPI releases
INSERT INTO news_events (occurred_at, category, label, source, metadata)
VALUES
  ('2024-02-13 13:30:00+00', 'economics', 'CPI release Jan 2024',   'BLS', '{"headline_yoy": 3.1, "core_yoy": 3.9}'),
  ('2024-03-12 12:30:00+00', 'economics', 'CPI release Feb 2024',   'BLS', '{"headline_yoy": 3.2, "core_yoy": 3.8}'),
  ('2024-04-10 12:30:00+00', 'economics', 'CPI release Mar 2024',   'BLS', '{"headline_yoy": 3.5, "core_yoy": 3.8}'),
  ('2024-05-15 12:30:00+00', 'economics', 'CPI release Apr 2024',   'BLS', '{"headline_yoy": 3.4, "core_yoy": 3.6}'),
  ('2024-06-12 12:30:00+00', 'economics', 'CPI release May 2024',   'BLS', '{"headline_yoy": 3.3, "core_yoy": 3.4}'),
  ('2024-07-11 12:30:00+00', 'economics', 'CPI release Jun 2024',   'BLS', '{"headline_yoy": 3.0, "core_yoy": 3.3}'),
  ('2024-08-14 12:30:00+00', 'economics', 'CPI release Jul 2024',   'BLS', '{"headline_yoy": 2.9, "core_yoy": 3.2}'),
  ('2024-09-11 12:30:00+00', 'economics', 'CPI release Aug 2024',   'BLS', '{"headline_yoy": 2.5, "core_yoy": 3.2}'),
  ('2024-10-10 12:30:00+00', 'economics', 'CPI release Sep 2024',   'BLS', '{"headline_yoy": 2.4, "core_yoy": 3.3}'),
  ('2024-11-13 13:30:00+00', 'economics', 'CPI release Oct 2024',   'BLS', '{"headline_yoy": 2.6, "core_yoy": 3.3}'),
  ('2024-12-11 13:30:00+00', 'economics', 'CPI release Nov 2024',   'BLS', '{"headline_yoy": 2.7, "core_yoy": 3.3}'),
  ('2025-01-15 13:30:00+00', 'economics', 'CPI release Dec 2024',   'BLS', '{"headline_yoy": 2.9, "core_yoy": 3.2}')
ON CONFLICT DO NOTHING;

-- Recent events (relative to NOW) used for NLP tagger training.
-- These fall within the 90-day training window and align with intraday signals.
INSERT INTO news_events (occurred_at, category, label, source, metadata)
VALUES
  (date_trunc('hour', NOW() - INTERVAL '13 hours'), 'fed', 'Fed rate watch signal A',   'market', '{}'),
  (date_trunc('hour', NOW() - INTERVAL '12 hours'), 'fed', 'Fed rate watch signal B',   'market', '{}'),
  (date_trunc('hour', NOW() - INTERVAL '11 hours'), 'fed', 'FOMC commentary excerpt C', 'reuters', '{}'),
  (date_trunc('hour', NOW() - INTERVAL '10 hours'), 'fed', 'FOMC commentary excerpt D', 'reuters', '{}'),
  (date_trunc('hour', NOW() - INTERVAL '9 hours'),  'fed', 'Treasury auction update E', 'treasury', '{}'),
  (date_trunc('hour', NOW() - INTERVAL '8 hours'),  'fed', 'Treasury auction update F', 'treasury', '{}'),
  (date_trunc('hour', NOW() - INTERVAL '7 hours'),  'fed', 'Fed funds rate watch G',    'bloomberg', '{}'),
  (date_trunc('hour', NOW() - INTERVAL '6 hours'),  'fed', 'Fed funds rate watch H',    'bloomberg', '{}'),
  (date_trunc('hour', NOW() - INTERVAL '5 hours'),  'fed', 'Inflation outlook signal I','bls', '{}'),
  (date_trunc('hour', NOW() - INTERVAL '4 hours'),  'fed', 'Inflation outlook signal J','bls', '{}'),
  (date_trunc('hour', NOW() - INTERVAL '3 hours'),  'fed', 'Rate path indicator K',     'market', '{}'),
  (date_trunc('hour', NOW() - INTERVAL '2 hours'),  'fed', 'Rate path indicator L',     'market', '{}')
ON CONFLICT DO NOTHING;

COMMIT;

SELECT COUNT(*) AS news_events_total FROM news_events;
