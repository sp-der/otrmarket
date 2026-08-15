#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

echo "OTR Market Operation 4 setup"

echo "[1/4] Activating Python environment..."
if [ ! -d .venv ]; then
  python -m venv .venv
fi
source .venv/bin/activate

echo "[2/4] Installing/verifying dependencies..."
python -m pip install -r requirements.txt

echo "[3/4] Running full test suite..."
python -m unittest discover -s tests -v

echo "[4/4] Migrating/verifying database..."
python scripts/verify_operation4.py

echo
echo "Operation 4 installed successfully."
echo "Run: bash run_all.sh"
echo "Then run NinjaTrader Market Replay with NQ + ES + GC bridge indicators attached."
