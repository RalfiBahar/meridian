#!/usr/bin/env bash
# Run every Meridian analytics / research pipeline step against live ingested data.
set -uo pipefail
cd "$(dirname "$0")/.."

log() { echo ""; echo "========== $* =========="; }

log "Health"
uv run python -m meridian.cli health

log "Analytics: signals (all open markets)"
uv run python -m meridian.cli analytics signals || true

log "Analytics: microstructure (NYC weather market)"
uv run python -m meridian.cli analytics microstructure KXHIGHNY-26JUN19-B83.5 --window 1 --write-signals || true

log "Analytics: microstructure (KXFED)"
uv run python -m meridian.cli analytics microstructure KXFED-27APR-T4.00 --window 1 --write-signals || true

log "Arb: group Fed partitions"
uv run python -m meridian.cli arb group-fed || true

log "Analytics: signals (refresh after grouping)"
uv run python -m meridian.cli analytics signals || true

log "Analytics: fedwatch + CME"
uv run python -m meridian.cli analytics fedwatch --cme || true

log "Arb: monitor + write signals"
uv run python -m meridian.cli arb monitor --write-signals || true

log "Backfill live settled markets from Kalshi for calibration"
bash scripts/backfill-real-settled-markets.sh || true

log "Analytics: calibrate (needs settled markets — may skip)"
uv run python -m meridian.cli analytics calibrate --write-signals || true

log "Analytics: anomaly detection"
uv run python -m meridian.cli analytics anomaly --write-signals || true

log "Analytics: regime detection"
uv run python -m meridian.cli analytics regime --write-signals || true

log "Analytics: NLP tagger"
uv run python -m meridian.cli analytics nlp-tag --price-threshold 0.02 --write-signals || true

log "Analytics: market maker backtest"
uv run python -m meridian.cli analytics marketmaker KXHIGHNY-26JUN19-B83.5 --window 1 || true

log "Experiment: kalshi_fed_pmf"
uv run python -m meridian.cli experiment run kalshi_fed_pmf || true

log "Experiment: arb_snapshot"
uv run python -m meridian.cli experiment run arb_snapshot || true

log "Experiment: portfolio walk-forward"
uv run python -m meridian.cli experiment portfolio --category unknown --walk-forward || true

log "Experiment list"
uv run python -m meridian.cli experiment list || true

log "DB summary"
uv run python -c "
import asyncio
from meridian.config import get_settings
from meridian.db.postgres import pool_context
async def main():
    async with pool_context(get_settings()) as pool:
        for t in ['markets','ticks','signals','experiments','market_groups']:
            n = await pool.fetchval(f'SELECT COUNT(*) FROM {t}')
            print(f'  {t}: {n}')
asyncio.run(main())
"

log "Done"
