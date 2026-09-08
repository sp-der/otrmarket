from __future__ import annotations

import json
import os

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
