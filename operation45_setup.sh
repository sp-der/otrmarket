#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

echo "OTR Market Operation 4.5"
echo "========================"

PYTHON="python"
if [ -x ".venv/bin/python" ]; then
  PYTHON=".venv/bin/python"
fi

"$PYTHON" -m compileall -q src tests
"$PYTHON" -m unittest discover -s tests -v
PYTHONPATH=. "$PYTHON" scripts/verify_operation45.py

if command -v node >/dev/null 2>&1; then
  node --check src/dashboard/static/app.js
fi

echo
echo "Operation 4.5 ready. Live broker execution remains disabled."
