# OTR Market — Operation 1: Strategy Engine Foundation

This update pivots OTR from a market radar into a research/paper-trading engine for the strategy:

1. Price reaches a PD array.
2. A signal occurs inside the PD array: liquidity sweep or SMT.
3. Displacement confirms direction.
4. A new FVG forms, ideally in the 50–79% retracement zone of the displacement range.
5. Paper entry is placed at the entry FVG midpoint.
6. Stop sits beyond the recent swing / swept level.
7. Target is the most recent opposing swing.

## What Operation 1 adds

- Live OHLC candle construction on 1m, 5m, 15m and 1h.
- Swing-high / swing-low detection.
- Bullish and bearish FVG detection.
- FVG-based PD-array touch detection.
- Liquidity sweep detection.
- NQ/ES proxy SMT detection using QQQ/SPY.
- Statistical displacement detection.
- 50–79% displacement retracement validation.
- Stateful confluence engine.
- Paper-only limit-entry simulation.
- Stop/target handling and R-multiple tracking.
- SQLite tables for candles, setups and paper trades.
- Live dashboard showing feeds, momentum and paper status.
- Unit tests.

## Important scope

Operation 1 deliberately supports **FVG as the PD-array type**. Order Blocks, Breakers and additional PD arrays come in later operations after this foundation is verified.

There is **no broker order execution** in Operation 1. The paper executor cannot send live orders.

## Install

Upload `otrmarket-operation-1.zip` to the root of your repo, then in Codespaces:

```bash
unzip -o otrmarket-operation-1.zip -d .
bash operation1_setup.sh
python -m src.main
```

Your existing `.env` stays untouched.

## Commit after verification

```bash
git add .
git commit -m "Operation 1: add ICT confluence strategy engine and paper execution"
git push origin main
```

## Operation 2 target

- Historical candle backfill so the engine does not need to wait hours/days to warm up.
- Persistent FVG lifecycle / mitigation tracking.
- Stronger swing and liquidity-pool model.
- Configurable strategy timeframes and session filters.
- Setup journal with explicit PASS/FAIL confluence reasons.
- Backtest runner across historical BTC / QQQ / SPY data.
