from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Running a file from scripts/ puts scripts/ at sys.path[0]. Add the repository
# root explicitly so the normal `src.*` imports work in CI and local shells.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.integrations.nautilus_shadow.probe import probe_nautilus


if __name__ == "__main__":
    report = probe_nautilus()
    print(json.dumps(report, indent=2, sort_keys=True))

    requested = os.getenv("OTR_NAUTILUS_SHADOW", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
        "enabled",
    }
    if requested and not report["active"]:
        raise SystemExit(1)
