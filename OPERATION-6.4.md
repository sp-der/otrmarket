# Operation 6.4 — Smart Reversal + Pre-Entry Viability

Operation 6.4 addresses two live-test failure modes seen on Aug. 18, 2026:

1. Strong lower-timeframe reversals being hard-blocked only because the primary higher timeframe had not flipped yet.
2. Fast moves being registered as pending setups after price had already traveled most or all of the planned objective.

## 6.4A — Countertrend reversal exception

A primary higher-timeframe disagreement is no longer an automatic veto for 1m/5m ICT confluence setups.

The exception is deliberately narrow. A countertrend candidate must have:

- SMT or confirmed liquidity sweep trigger
- at least 1.75R planned room
- at least 1.75x displacement body and 1.40x displacement range
- fresh entry FVG, no more than 2 execution bars old
- Operation 6.4 countertrend score of at least 80/100

Risk is always reduced:

- 50% cap when the intermediate + narrative timeframes both support the setup
- 45% cap when at least one supports it
- 35% cap when the reversal is fighting the broader narrative and only the local reversal evidence is strong enough

All active-risk, cooldown, post-loss, and evaluation-guard protections remain in force.

## 6.4B — Live pre-entry viability

Immediately before a setup enters the pending paper order book, Operation 6.4 compares the actual live market price with the planned entry, stop, and target.

The existing 75% no-chase threshold is preserved.

If price has already traveled 75% or more of the planned objective before registration:

- the original order is never added to the pending book
- the setup is labeled `MISSED_EXTENDED`
- the trade ledger keeps invalidated accounting semantics with $0 realized P&L
- the continuation engine is armed immediately for a fresh pullback + resumption opportunity

If the protective stop was already broken, the setup remains a true invalidation rather than a missed-move classification.

## Deployment test gate

Railway runs the Operation 6.3 and 6.4 regression suites before future production deploys. A failed regression blocks the new release from replacing the healthy production instance.

## Result

The bot remains selective, but it is less rigid around legitimate reversals and clearer about the difference between:

- a bad setup
- a good countertrend setup taken at reduced risk
- a valid setup detected after the move was already gone
- an actually invalidated trade thesis
