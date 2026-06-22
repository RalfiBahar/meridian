#!/usr/bin/env bash
# Kill stale processes, reset Docker volumes, boot full Meridian dev stack.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Load discovered ingest tickers for docker compose substitution.
if [[ -f config/ingest.env ]]; then
  set -a
  # shellcheck disable=SC1091
  source config/ingest.env
  set +a
fi

echo "==> Stopping host processes..."
pkill -f "meridian.cli serve" 2>/dev/null || true
pkill -f "meridian.cli ingest" 2>/dev/null || true
pkill -f "next dev" 2>/dev/null || true
sleep 2

echo "==> Stopping Docker stack (wipe volumes)..."
docker compose down -v

echo "==> Booting Docker (DB, Redis, ingest, Grafana, Prometheus)..."
docker compose up -d --build --wait

echo "==> Applying migrations..."
uv run python -m meridian.cli migrate

echo "==> Waiting 45s for ingest to populate markets..."
sleep 45

echo "==> Running analytics pipeline..."
bash scripts/run-full-pipeline.sh

echo "==> Starting API on :8000 (logs: /tmp/meridian-api.log)..."
nohup uv run python -m meridian.cli serve --reload --port 8000 --metrics-port 0 \
  > /tmp/meridian-api.log 2>&1 &
sleep 3

if [[ ! -f frontend/.env.local ]]; then
  cp frontend/.env.local.example frontend/.env.local
fi

echo "==> Starting frontend on :3001 (logs: /tmp/meridian-frontend.log)..."
cd frontend
nohup npm run dev -- -p 3001 > /tmp/meridian-frontend.log 2>&1 &
cd "$ROOT"
sleep 5

echo ""
echo "=========================================="
echo "  Meridian is up"
echo "=========================================="
curl -sf http://localhost:8000/health && echo "  API:      http://localhost:8000/health"
echo "  Frontend: http://localhost:3001/markets"
echo "  Grafana:  http://localhost:3000  (ingest dashboard)"
echo "  Prometheus: http://localhost:9090"
echo ""
docker compose ps --format "table {{.Name}}\t{{.Status}}"
echo ""
uv run python -c "
import asyncio
from meridian.config import get_settings
from meridian.db.postgres import pool_context
async def main():
    async with pool_context(get_settings()) as pool:
        for t in ['markets','ticks','signals','experiments']:
            n = await pool.fetchval(f'SELECT COUNT(*) FROM {t}')
            print(f'  {t}: {n}')
asyncio.run(main())
"
echo ""
echo "Logs: tail -f /tmp/meridian-api.log /tmp/meridian-frontend.log"
echo "Stop:  pkill -f 'meridian.cli serve'; pkill -f 'next dev'; docker compose down"
