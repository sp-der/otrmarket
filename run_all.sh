#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# Local development uses .venv. Production containers (Railway) use the
# system Python environment created during the image build.
if [ -f ".venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

PYTHON_BIN="${PYTHON_BIN:-python}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  PYTHON_BIN="python3"
fi

mkdir -p data

# Railway injects PORT dynamically. Keep DASHBOARD_PORT for local/Codespaces.
export DASHBOARD_HOST="${DASHBOARD_HOST:-0.0.0.0}"
if [ -n "${PORT:-}" ]; then
  export DASHBOARD_PORT="$PORT"
else
  export DASHBOARD_PORT="${DASHBOARD_PORT:-8000}"
fi

cleanup() {
  if [ -n "${ENGINE_PID:-}" ] && kill -0 "$ENGINE_PID" 2>/dev/null; then
    kill "$ENGINE_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

echo "Starting OTR strategy engine in background..."
"$PYTHON_BIN" -m src.main > data/engine.log 2>&1 &
ENGINE_PID=$!

echo "Strategy engine PID: $ENGINE_PID"
echo "Engine log: data/engine.log"
echo "Starting web dashboard on port ${DASHBOARD_PORT}..."
exec "$PYTHON_BIN" -m src.dashboard.server
