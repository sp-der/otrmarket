#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# Local development uses .venv when available. Production containers use the
# system Python environment created during the Docker build.
if [ -f ".venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

PYTHON_BIN="${PYTHON_BIN:-python}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  PYTHON_BIN="python3"
fi

mkdir -p data

# Railway injects PORT dynamically. Keep DASHBOARD_PORT for local use.
export DASHBOARD_HOST="${DASHBOARD_HOST:-0.0.0.0}"
if [ -n "${PORT:-}" ]; then
  export DASHBOARD_PORT="$PORT"
else
  export DASHBOARD_PORT="${DASHBOARD_PORT:-8000}"
fi

echo "Starting OTR Market Operation 8.1 on port ${DASHBOARD_PORT}..."
exec "$PYTHON_BIN" -m src.dashboard.server_81
