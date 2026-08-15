#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

python -m compileall -q src tests
python -m unittest discover -s tests -v
python scripts/verify_operation44.py

if command -v node >/dev/null 2>&1; then
  node --check src/dashboard/static/app.js
fi

echo "Operation 4.4 verification: OK"
echo "Next: commit/push. Railway should redeploy automatically from main."
