#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [ -f ".venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

PYTHON_BIN="${PYTHON_BIN:-python}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  PYTHON_BIN="python3"
fi

exec "$PYTHON_BIN" -m src.dashboard.server_81
