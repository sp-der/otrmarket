#!/usr/bin/env bash
set -euo pipefail

echo "OTR Market Operation 4.4.1"
echo "=========================="

python - <<'PY'
from pathlib import Path

root = Path(".")
html = root / "src/dashboard/static/index.html"
favicon = root / "src/dashboard/static/favicon.png"
apple = root / "src/dashboard/static/apple-touch-icon.png"

assert html.exists(), "Dashboard index.html missing"
text = html.read_text()
assert '/market/assets/favicon.png?v=4.4.1' in text, "Favicon tag missing"
assert '/market/assets/apple-touch-icon.png?v=4.4.1' in text, "Apple icon tag missing"
assert favicon.exists() and favicon.stat().st_size > 0, "favicon.png missing"
assert apple.exists() and apple.stat().st_size > 0, "apple-touch-icon.png missing"

print("Favicon assets: OK")
print("Operation 4.4.1 verification: OK")
PY
