# OTR Market Operation 4.2

Combined Operation 4.1 reliability fixes + Scanner UI redesign.

## Operation 4.1 included

This ZIP is standalone over Operation 4. You do **not** need to install the separate Operation 4.1 ZIP first.

- Stage-specific expiry timers:
  - `WAIT_SIGNAL`: 16 bars
  - `WAIT_DISPLACEMENT`: 8 bars
  - `WAIT_ENTRY_FVG`: 8 bars
- The timer resets each time the setup advances a stage.
- Genuine stage timeouts report `EXPIRED` with an explanation.
- During NinjaTrader futures replay, BTC quotes/candles remain stored and visible, but BTC strategy scanning and BTC paper-position updates are paused.
- The `ingested_at` legacy database migration/index ordering is permanently corrected.

## Operation 4.2 Scanner redesign

The full Scanner is now organized by market instead of one long mixed list:

- NQ Futures
- ES Futures
- Gold Futures
- Bitcoin

Each market contains fixed cards for:

- 1m
- 5m
- 15m
- 1h

Each timeframe card shows:

- direction
- current stage
- score out of 6
- market time
- trigger
- current reasoning note
- visual six-stage progress rail:
  `PD -> Signal -> Displacement -> FVG -> 50-79 -> R:R`

The market header summarizes the best current state for that instrument. During futures replay, the BTC section remains visible and explicitly shows replay isolation rather than mixing live BTC strategy states into historical futures testing.

Existing market and timeframe filters still work.

## Dashboard cache fix

Dashboard CSS and JS now use a `?v=4.2` cache-buster so Codespaces/browser caching cannot leave the new Scanner markup paired with stale styles.

## Safety

- No live broker order execution is added.
- Paper mode remains the execution boundary.
- Existing market history, setups, paper trades, and `.env` are preserved.
