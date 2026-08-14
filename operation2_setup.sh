#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

echo ""
echo "=============================================="
echo " OTR MARKET - OPERATION 2 DASHBOARD SETUP"
echo "=============================================="
echo ""

if [ ! -d ".venv" ]; then
  echo "Creating Python virtual environment..."
  python -m venv .venv
fi

source .venv/bin/activate

echo "Installing dashboard dependencies..."
python -m pip install -r requirements.txt

echo ""
echo "Running Operation 1 + Operation 2 tests..."
python -m unittest discover -s tests -v

echo ""
echo "Verifying dashboard against the current OTR database..."
python scripts/verify_operation2.py

echo ""
echo "Operation 2 installed successfully."
echo ""
echo "Start dashboard only:"
echo "  bash run_dashboard.sh"
echo ""
echo "Start trading engine + dashboard:"
echo "  bash run_all.sh"
echo ""
