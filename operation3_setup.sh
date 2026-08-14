#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  python -m venv .venv
fi
source .venv/bin/activate

python -m pip install -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
fi

python - <<'PY'
from pathlib import Path
import secrets

path = Path('.env')
lines = path.read_text().splitlines()
found = False
output = []
for line in lines:
    if line.startswith('OTR_BRIDGE_KEY='):
        found = True
        value = line.split('=', 1)[1].strip()
        if not value:
            line = 'OTR_BRIDGE_KEY=' + secrets.token_urlsafe(32)
    output.append(line)
if not found:
    output.append('OTR_BRIDGE_KEY=' + secrets.token_urlsafe(32))
path.write_text('\n'.join(output) + '\n')
PY

python -m compileall -q src
python -m unittest discover -s tests -v
python scripts/verify_operation3.py

echo
echo "Operation 3 installed."
echo "Your NinjaTrader bridge key is stored only in .env."
echo "Run this when you need to copy it into NinjaTrader:"
echo "  grep '^OTR_BRIDGE_KEY=' .env"
echo
echo "IMPORTANT: Do not commit .env."
