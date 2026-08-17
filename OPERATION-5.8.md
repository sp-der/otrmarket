# Operation 5.8 - Adaptive Trade Intelligence

Operation 5.8 expands the number of legitimate ways OTR can recognize a trade without removing the existing prop-risk guardrails.

## Execution changes

- Adds an independent **MSS/CHOCH Reversal Engine** on 5m and 15m.
- Reversal flow: confirmed swing break -> strong displacement -> first controlled pullback -> FVG / 70.5% OTE / 79% OTE -> structural target.
- Reversal displacement is never chased. If price does not pull back into an approved level, the setup waits and expires.
- Higher-timeframe direction becomes a scoring input for reversal setups instead of an automatic veto. Countertrend reversals require A grade (80+) and run at reduced risk.
- Reversal A/A+ starts at 60-75% of the normal risk cap while the new setup family builds evidence. B+ keeps the existing 40% reduced tier and 2.50R requirement.
- Existing ICT continuation and 10/10 Rejection Block engines remain intact.
- 1m NQ/ES no longer requires SMT as the only confirmation path. A non-SMT 1m setup must have a liquidity sweep, >=1.90x body displacement, >=1.50x range displacement, and >=1.50R, and runs at no more than 70% risk.

## Session changes

- Original selected weekday session (default 09:30-13:00 ET): full-risk core.
- Extended weekday execution (default 08:30-15:30 ET): qualified setups may execute at up to 65% risk outside the core window.
- Sunday Globex remains reduced at 40%.
- The bot continues scanning and learning outside execution hours even when the session governor does not allow new risk.
- Existing daily trade cap, daily loss stop, cooldowns, correlated NQ/ES exposure protection, geometry checks, and Evaluation Guard remain active.

Optional settings:

- `OTR_EXTENDED_SESSION_START=08:30`
- `OTR_EXTENDED_SESSION_END=15:30`
- `OTR_ENGINE_MODULE=src.main_58`

## Market Learning Observer

Operation 5.8 adds persistent retrospective market learning. On completed 5m, 15m, and 30m windows it looks for meaningful directional excursions. For NQ the floor is 30 points, with a volatility-adjusted threshold of at least 1.8x recent average range.

For each qualifying move it stores a lesson containing evidence visible near the beginning of the move:

- pre-move market regime
- liquidity sweep
- SMT
- MSS / CHOCH
- displacement body/range strength
- FVG formation
- whether the live strategy produced a candidate
- candidate status and the gate that blocked it

The observer builds persistent feature-frequency statistics across lessons and survives evaluation resets. This is **learning memory**, not unattended self-modification: it does not rewrite live thresholds or risk by itself. Promotion of learned patterns into execution remains controlled and testable.

A protected endpoint is available at `/market/api/learning` for reviewing stored lessons and common early-move features.

## Dashboard cleanup

The large Operation 5.3 Trade Intelligence research panel is removed from the normal dashboard view. Its underlying MFE/MAE and shadow-trade data remains stored and queryable through `/market/api/intelligence` when needed.
