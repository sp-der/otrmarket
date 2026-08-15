#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "Missing .venv. Run this patch from the otrmarket repo root."
  exit 1
fi

source .venv/bin/activate

# Operation 4.1 database repair is included in this combined patch.
python - <<'PY'
from src.storage.database import get_connection
con = get_connection()
con.close()
print("Database migration/index check: OK")
PY

python -m unittest discover -s tests -v
python scripts/verify_operation42.py
node --check src/dashboard/static/app.js
python -m compileall -q src tests

echo
echo "Operation 4.2 installed successfully."
echo "Includes ALL Operation 4.1 fixes plus the scanner redesign."
echo
echo "Operation 4.1 included:"
echo "  - stage-specific setup expiry timers"
echo "  - BTC strategy isolation during futures replay"
echo "  - permanent ingested_at migration ordering fix"
echo
echo "Operation 4.2 included:"
echo "  - scanner grouped into NQ / ES / GC / BTC sections"
echo "  - 1m / 5m / 15m / 1h cards inside every market"
echo "  - six-stage visual progress rail"
echo "  - cleaner stage, trigger, score, market-time hierarchy"
echo "  - BTC replay-isolation state shown clearly"
echo "  - active setup summaries per market"
echo "  - cache-busted dashboard assets so the new CSS/JS loads immediately"
echo
echo "Start OTR with: bash run_all.sh"
