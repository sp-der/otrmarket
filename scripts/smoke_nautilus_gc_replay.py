from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.integrations.nautilus_shadow.gold_replay import StoredGoldTick, replay_gold_ticks


if __name__ == "__main__":
    start = datetime(2026, 9, 1, 13, 30, tzinfo=timezone.utc)
    ticks = [
        StoredGoldTick(start + timedelta(seconds=index), "ninjatrader:MGC DEC26", 3500.0 + index * 0.1,
                       3499.9 + index * 0.1, 3500.1 + index * 0.1)
        for index in range(8)
    ]
    report = replay_gold_ticks(ticks)
    print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    if not report.engine_ran or report.input_ticks != len(ticks) or report.contract_family != "MGC":
        raise SystemExit(1)
