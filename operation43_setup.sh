#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

python -m compileall -q src tests
python -m unittest discover -s tests -v
python scripts/verify_operation42.py

echo "Operation 4.3 verification: OK"
echo "Next: commit/push, then deploy the GitHub repo to Railway."
