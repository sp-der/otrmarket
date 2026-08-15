# OTR Market Operation 4 — Strategy Lab

Operation 4 turns the futures bridge into a replay-aware strategy research engine.

## What changed

- Separates **market event time** from **server ingest time** so NinjaTrader Market Replay shows as `REPLAY` instead of looking 40+ hours stale.
- Momentum calculations now use market event timestamps, so 1s / 5s / 15s / 1m moves work during replay.
- Restores completed candles from SQLite on engine startup.
- Persists the last processed NinjaTrader quote ID so engine restarts do not silently skip/duplicate bridge processing.
- Tracks active FVGs and removes fully mitigated FVGs from PD-array candidates.
- Synchronizes NQ / ES candles by shared close-time for SMT detection.
- Re-evaluates NQ / ES after the paired market closes so SMT can be recognized regardless of which feed crosses the minute first.
- Prevents signal -> displacement -> entry-FVG stages from advancing multiple times on the same candle.
- Uses replay market timestamps for setup creation and paper-trade timing.
- Adds a persistent `strategy_diagnostics` table.
- Adds a dashboard **Scanner** showing:
  - PD Array
  - Signal (liquidity sweep or SMT)
  - Displacement
  - Entry FVG
  - 50–79% retracement
  - Risk / Reward
  - current stage and note
- Dashboard market cards show `REPLAY`/`LIVE` based on actual ingress activity, while retaining historical market timestamps.

## Still intentionally disabled

Operation 4 does **not** send live or simulated broker orders. Paper execution remains internal to OTR.
NinjaTrader SIM order execution is reserved for the next operation after the scanner is validated against replay.

## Install

Stop `run_all.sh` first, then unzip Operation 4 over the repository and run:

```bash
bash operation4_setup.sh
```

Start OTR:

```bash
bash run_all.sh
```

Run NQ + ES + GC Market Replay through the NinjaTrader bridge and open `/market`.
