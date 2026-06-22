#!/usr/bin/env bash
# One-command setup: clone → configure → discover popular markets → boot full stack.
#
# Usage:
#   bash scripts/setup.sh
#
# Prerequisites: Docker (Compose v2), Python 3.12, uv, Node.js 20+.
# Kalshi API credentials in .env for live ingest (see docs/getting-started.md).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

info() { echo "==> $*"; }
warn() { echo "WARNING: $*" >&2; }

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "error: '$1' not found — install it and re-run setup." >&2
    exit 1
  fi
}

info "Checking prerequisites..."
need_cmd docker
need_cmd uv
need_cmd node
need_cmd npm
docker compose version >/dev/null

info "Installing Python dependencies..."
uv sync

info "Installing frontend dependencies..."
(cd frontend && npm install --no-audit --no-fund)

if [[ ! -f .env ]]; then
  info "Creating .env from .env.example..."
  cp .env.example .env
  warn "Edit .env with your Kalshi credentials (MERIDIAN_KALSHI_ACCESS_KEY + PEM path)."
fi

# Ensure Kalshi PEM directory exists.
KEY_DIR="${HOME}/.config/meridian"
mkdir -p "$KEY_DIR"
if [[ ! -f "$KEY_DIR/kalshi-private-key.pem" ]]; then
  warn "Kalshi private key not found at $KEY_DIR/kalshi-private-key.pem"
  warn "Ingest will fail until you add credentials — see docs/getting-started.md §6."
fi

if [[ ! -f config/ingest.env ]]; then
  cp config/ingest.env.example config/ingest.env
fi

info "Discovering popular live markets (Kalshi + Polymarket)..."
if ! uv run python -m meridian.cli markets discover --write-env config/ingest.env; then
  warn "Market discovery failed — using config/ingest.env.example defaults."
  cp config/ingest.env.example config/ingest.env
fi

info "Booting full dev stack..."
bash scripts/dev-up.sh

info "Setup complete."
