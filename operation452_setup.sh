#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

echo "OTR Market Operation 4.5.2"
echo "==========================="
echo "Storage guard + Railway runtime recovery"

PYTHON="python"
if [ -x ".venv/bin/python" ]; then
  PYTHON=".venv/bin/python"
fi

"$PYTHON" -m compileall -q src tests
"$PYTHON" -m unittest discover -s tests -v
PYTHONPATH=. "$PYTHON" scripts/verify_operation452.py

echo
echo "Operation 4.5.2 ready. Raw ticks are now bounded; candles/trades remain persistent."
