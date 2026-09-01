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

# Operation 7.2N keeps the Operation 7.2 strategy engine while giving the
# dashboard a first-class continuous VERIFY mode and a complete trade ledger.
# Broker transmission remains disabled unless the explicit execution
# interlocks are armed.
echo "Starting OTR Operation 7.2N supervised runtime on port ${DASHBOARD_PORT}..."
exec "$PYTHON_BIN" -m src.dashboard.server_72n
