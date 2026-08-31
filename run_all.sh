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

# One-shot guarded Operation 7.2S ledger cleanup. The script is idempotent and
# refuses to touch anything unless the only matching residual losses are the
# exact ES and NQ -$125 MOMENTUM_SCALP rows. Running it here guarantees the
# production volume is mounted, unlike Railway's isolated predeploy phase.
if [ -f "scripts/remove_72s_losses_once.py" ]; then
  echo "Running guarded 7.2S ledger cleanup against mounted production volume..."
  "$PYTHON_BIN" scripts/remove_72s_losses_once.py
fi

# Railway injects PORT dynamically. Keep DASHBOARD_PORT for local/Codespaces.
export DASHBOARD_HOST="${DASHBOARD_HOST:-0.0.0.0}"
if [ -n "${PORT:-}" ]; then
  export DASHBOARD_PORT="$PORT"
else
  export DASHBOARD_PORT="${DASHBOARD_PORT:-8000}"
fi

# Operation 7.2 promotes every older strategy pin to the current Market
# Intelligence + fail-closed execution runtime. Broker transmission remains
# disabled unless the explicit Operation 7.2 execution interlocks are armed.
echo "Starting OTR Operation 7.2 supervised runtime on port ${DASHBOARD_PORT}..."
exec "$PYTHON_BIN" -m src.dashboard.server_72
