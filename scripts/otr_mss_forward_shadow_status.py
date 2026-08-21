#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research.mss_forward_shadow import forward_shadow_snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only MSS Forward Shadow V1 status")
    parser.add_argument("--db", default="data/otrmarket.db")
    args = parser.parse_args()
    snapshot = forward_shadow_snapshot((ROOT / args.db).resolve())
    print(json.dumps(snapshot, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
