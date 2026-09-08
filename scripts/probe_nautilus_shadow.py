from __future__ import annotations

import json

from src.integrations.nautilus_shadow.probe import probe_nautilus


if __name__ == "__main__":
    print(json.dumps(probe_nautilus(), indent=2, sort_keys=True))
