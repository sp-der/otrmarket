#!/usr/bin/env bash
set -euo pipefail

if [[ -f .venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
else
  echo "No .venv found. Creating one..."
  python -m venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python scripts/verify_operation1.py

echo
echo "Operation 1 installed and verified."
echo "Start OTR with: python -m src.main"
