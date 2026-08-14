#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
source .venv/bin/activate
mkdir -p data

cleanup() {
  if [ -n "${ENGINE_PID:-}" ] && kill -0 "$ENGINE_PID" 2>/dev/null; then
    kill "$ENGINE_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

echo "Starting OTR strategy engine in background..."
python -m src.main > data/engine.log 2>&1 &
ENGINE_PID=$!

echo "Strategy engine PID: $ENGINE_PID"
echo "Engine log: data/engine.log"
echo "Starting web dashboard on port ${DASHBOARD_PORT:-8000}..."
exec python -m src.dashboard.server
