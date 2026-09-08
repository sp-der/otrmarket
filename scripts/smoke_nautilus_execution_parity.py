from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.integrations.nautilus_shadow.execution_parity import ShadowBracketIntent, simulate_gold_bracket
from src.integrations.nautilus_shadow.gold_replay import StoredGoldTick


if __name__ == "__main__":
    start = datetime(2026, 9, 1, 13, 30, tzinfo=timezone.utc)
    ticks = [
        StoredGoldTick(start, "ninjatrader:MGC DEC26", 3501.0, 3500.9, 3501.1),
        StoredGoldTick(start + timedelta(seconds=1), "ninjatrader:MGC DEC26", 3500.0, 3499.9, 3500.0),
        StoredGoldTick(start + timedelta(seconds=2), "ninjatrader:MGC DEC26", 3502.0, 3501.9, 3502.1),
        StoredGoldTick(start + timedelta(seconds=3), "ninjatrader:MGC DEC26", 3504.0, 3504.0, 3504.1),
        StoredGoldTick(start + timedelta(seconds=4), "ninjatrader:MGC DEC26", 3504.1, 3504.0, 3504.2),
    ]
    intent = ShadowBracketIntent(
        setup_id="synthetic-mgc-win",
        direction="bullish",
        entry_price=3500.0,
        stop_price=3498.0,
        target_price=3504.0,
        quantity=1,
        execution_contract="MGC DEC26",
    )
    result = simulate_gold_bracket(ticks, intent)
    print(json.dumps(result.to_dict(), indent=2, sort_keys=True))

    if result.status != "CLOSED":
        raise SystemExit(f"Expected CLOSED result, got {result.status}")
    if result.result != "WIN":
        raise SystemExit(f"Expected WIN result, got {result.result}")
    if result.entry_fill_price is None or not math.isclose(result.entry_fill_price, 3500.0, abs_tol=0.11):
        raise SystemExit(f"Unexpected entry fill {result.entry_fill_price}")
    if result.exit_fill_price is None or not math.isclose(result.exit_fill_price, 3504.0, abs_tol=0.11):
        raise SystemExit(f"Unexpected exit fill {result.exit_fill_price}")
    if result.result_r is None or not math.isclose(result.result_r, 2.0, abs_tol=0.06):
        raise SystemExit(f"Unexpected R result {result.result_r}")
    if result.result_dollars is None or not math.isclose(result.result_dollars, 40.0, abs_tol=1.1):
        raise SystemExit(f"Unexpected MGC P/L {result.result_dollars}")
    if len(result.orders) != 3:
        raise SystemExit(f"Expected 3 bracket orders, got {len(result.orders)}")
