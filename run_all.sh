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

# Operation 7.2Q is the canonical Gold verification runtime. It preserves the
# 7.2N VERIFY dashboard/full ledger and 7.2O fixed-risk accounting, adds the
# final GC 1m quality firewall, and contains no loss-pruning startup hooks.
echo "Starting OTR Operation 7.2Q supervised runtime on port ${DASHBOARD_PORT}..."
exec "$PYTHON_BIN" -m src.dashboard.server_72q
