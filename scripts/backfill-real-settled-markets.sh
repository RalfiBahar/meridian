#!/usr/bin/env bash
# Backfill ACTUALLY settled Kalshi markets via the Kalshi REST API.
# Requires Kalshi credentials in .env (same as ingest).
#
# Usage:
#   bash scripts/backfill-real-settled-markets.sh [--category fed] [--dry-run]
#
# After running:
#   uv run python -m meridian.cli analytics calibrate --category fed --write-signals

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

DRY_RUN=0
CATEGORIES=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --category) CATEGORIES+=("$2"); shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

log() { echo "[backfill-real-settled-markets] $*"; }

CMD=(uv run python -m meridian.cli markets sync-settled)
for c in "${CATEGORIES[@]}"; do
  CMD+=(--category "$c")
done

if [[ "$DRY_RUN" -eq 1 ]]; then
  log "DRY RUN — would run: ${CMD[*]}"
  exit 0
fi

log "Syncing live settled markets from Kalshi..."
"${CMD[@]}"

log "Done. Run: uv run python -m meridian.cli analytics calibrate --category fed --write-signals"
