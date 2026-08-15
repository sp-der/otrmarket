#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

echo "OTR Market Operation 4.5.1"
echo "==========================="

PYTHON="python"
if [ -x ".venv/bin/python" ]; then
  PYTHON=".venv/bin/python"
fi

"$PYTHON" -m compileall -q src tests
"$PYTHON" -m unittest discover -s tests -v
PYTHONPATH=. "$PYTHON" scripts/verify_operation451.py

echo
echo "Operation 4.5.1 ready. Railway will now treat an engine death as a service failure."
