# Operation 4.7 - Multi-Strategy Rejection Block 10/10

Operation 4.7 adds a second independent setup engine without removing the existing ICT confluence model.

## Strategy engines

- `ICT_CONFLUENCE`: existing PD array / signal / displacement / FVG model.
- `REJECTION_BLOCK_10_10`: strict A+ rejection-block model.

Both engines receive the same completed candle histories. If both produce a setup on the same symbol, timeframe, and evaluation cycle, the runtime emits one candidate rather than stacking duplicate risk. The 10/10 rejection-block candidate gets priority and records the other strategy in metadata.

## Rejection Block 10/10 gate

A trade setup is emitted only when all ten items pass:

1. Directional bias is established from higher-timeframe confirmed structure.
2. Meaningful confirmed swing liquidity is identified.
3. Matching liquidity is swept.
4. The sweep candle forms a clean rejection block with a defined invalidation.
5. NQ/ES SMT correlation is checked on synchronized candles. SMT may be absent, but the check cannot be skipped or manufactured. Other markets mark correlation as not required.
6. A later strong same-direction displacement candle creates an FVG.
7. MSS/BOS closes through the pre-sweep short-term structure level. The displacement candle may itself supply the break.
8. A later candle retraces into the planned rejection-block/FVG area and closes inside it. No chase entries.
9. Entry and stop are defined before execution, with stop beyond rejection invalidation.
10. Confirmed opposing liquidity must offer at least 3R.

A 9/10 setup is explicitly rejected and the sequence must restart from fresh liquidity.

## Capital and execution

Operation 4.7 continues to use the existing Evaluation Risk Guard and Paper Executor. No live broker orders are enabled.

- Base modeled risk per trade: `$250`.
- 3R modeled objective: `$750`.
- Existing `$750` internal daily stop remains unchanged.
- Target geometry is calculated from tick-aligned entry/stop prices so executable rounding cannot quietly reduce a 3R setup below 3R.

## Runtime

`src/dashboard/server.py` now starts `src.main_multi`, which wraps the existing collectors/replay engine and replaces only the strategy coordinator. Replay rewind handling, candle persistence, NinjaTrader bridge consumption, dashboard service, evaluation controls, and paper trade accounting remain on the existing runtime path.

## Research metadata

Completed rejection-block setups persist:

- strategy name
- 10/10 checklist
- liquidity level and sweep time
- rejection block and invalidation
- SMT checked/present state
- MSS/BOS level
- entry area and FVG
- bias timeframe and rationale
- entry, stop, target, and final R:R

This makes strategy-by-strategy performance analysis possible without treating an A+ setup as a guaranteed winner.
