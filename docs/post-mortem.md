# Engineering notes — Meridian

> **Status:** Updated Phase 11 / E8. Incidents documented; performance numbers from dev stack.

---

## Schema & data model

- **Canonical events**: Single pydantic discriminated union (`QuoteEvent`, `BookEvent`,
  `TradeEvent`, …) so Kalshi and Polymarket share one ingest path.
- **Market IDs**: UUIDv5 from venue ticker — deterministic replays and idempotent
  upserts.
- **TimescaleDB hypertables**: `ticks`, `book_snapshots` time-partitioned; OLTP
  tables (`markets`, `signals`, `experiments`) on same Postgres instance.

---

## Incidents & fixes (examples to expand)

### JSONB metrics parsing (A1)

`meridian experiment list` crashed when asyncpg returned `metrics` JSONB as a string.
Fix: parse JSON in the CLI layer before pydantic validation.

### NLP signal pipeline (2026-06-19)

Three bugs in `analytics/nlp.py`: wrong timestamp column (`signal_ts` → `event_ts`),
invalid LATERAL join, `event_id` FK misuse. Fixed in agent session; see CHANGELOG.

### Agent-loop false positives

Stale bash wrappers from Claude Code tool runs (`npm run dev`) blocked `agent-loop`
start — fixed in `agent-platform/bin/agent-loop` process detection.

---

## Observability

- **Prometheus**: `ingest_events_total`, `ingest_lag_seconds`, reconnect counters.
- **Grafana**: `docker/grafana/dashboards/meridian-ingest.json`
- **Structured logs**: structlog JSON in prod, console in dev.

---

## Performance notes

| Metric | Value |
|--------|-------|
| Ingest msg/s | ~120 (Kalshi dev replay) |
| p99 ingest lag | < 50 ms |
| Tick count (DB) | 500K+ (seeded + ingest) |
| API p99 latency | < 30 ms (health/markets endpoints) |
| Calibration runtime | < 2 s (5 markets, 100 signals) |
| Walk-forward (60d train / 21d test) | < 5 s |

### Incident: JSONB metrics string coercion (2026-06-19)

`meridian experiment list` raised `AttributeError: 'str' object has no attribute 'items'`
because asyncpg returns JSONB columns as strings when the Python driver has no
registered codec.  Fixed by parsing in the CLI layer before pydantic validation
(`json.loads()` call in `_run_list`).

### Incident: NLP signal pipeline incorrect columns (2026-06-19)

Three bugs in `analytics/nlp.py`: wrong timestamp column (`signal_ts` → `event_ts`),
invalid LATERAL join referencing non-existent alias, `event_id` FK misuse.  All
three caused silent query failures; pipeline ran but wrote zero `market_moving_prob`
rows.  Fixed in same session; confirmed by C5 gate passing.

### Incident: KXFED semantics mismatch (2026-06-21)

Kalshi KXFED contracts use `_next_kxfed_date` in the API but internal column name
differed, causing the FedWatch implied-PMF endpoint to return empty results.
Fix: aligned field names in the API schema and frontend (`fomc_date` everywhere).

### Incident: Agent-loop false-positive process detection (2026-06-19)

Stale `npm run dev` bash wrapper left by a previous Claude Code tool run was
detected as the frontend process, blocking `dev-up.sh` restart logic.  Fixed by
tightening the process grep to match the Next.js server port directly.
