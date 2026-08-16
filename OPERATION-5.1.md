# Operation 5.1 - Session & Consistency Engine

Operation 5.1 adds a funded-style calibration layer on top of the existing Operation 5.0 A+ context filter.

## Goals

- Produce cleaner, repeatable trading days instead of chasing maximum daily P/L.
- Make short 3-day replay tests useful without forcing low-quality trades.
- Keep all non-selected timeframe candidates visible as shadow research.
- Stop adding risk once a reasonable base winning day has been secured.
- Make a post-loss second trade materially harder to qualify.

## Default calibration profile

- Trading timezone: `America/New_York`
- Execution timeframe: `5m`
- Entry window: `09:30-13:00` local session time
- Maximum executed trades per day: `2`
- Base winning-day lock: `+$250` realized P/L
- After one loss, the next ICT setup must offer at least `2.00R` with `1.90x` body and `1.50x` range displacement.
- Rejection Block remains eligible after a loss only at full `10/10` and `3R+`.

These are research defaults and can be changed through Railway environment variables documented in `.env.example`.

## Important behavior

Operation 5.1 does **not** force one trade per day. Every day can end with no executed trade if the selected window/timeframe never produces a setup that passes the full A+ context rules. Rejected candidates are still saved in `strategy_setups` as `QUALITY_BLOCKED` shadow candidates so the replay can be audited.

The existing Operation 5.0 rules remain active after the session filter, including higher-timeframe bias, displacement quality, fresh FVG requirements, stale pending-order expiry, global post-loss reset, same-market cooldown, and NQ/ES correlated-exposure protection.
