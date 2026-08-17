# Operation 5.5 - Chart Intelligence v1

Operation 5.5 adds the missing 30-minute market narrative used to grade lower-timeframe entries.

## Changes

- Builds and persists 30m candles from the live tick stream.
- Grades complete ICT candidates on a 0-100 scale with A+, A, B+ and research tiers.
- Scores local structure, 30m narrative, displacement, entry freshness, trigger quality, entry location and target room.
- Keeps hard blocks for invalid geometry, countertrend local structure, weak displacement and stale FVGs.
- Allows an SMT-confirmed reversal to qualify against an opposing 30m narrative when the full score still reaches A grade.
- Treats 30m warmup as advisory after a restart so the bot is not silenced while the new timeframe fills.
- Stores the score, grade, component breakdown and narrative conflict in each setup payload for missed-opportunity audits.

## Execution policy

- A / A+ (80+): may proceed to the existing session, exposure and evaluation guards.
- B+ and below: saved as `QUALITY_BLOCKED` research; never receives paper risk.
- Existing $250 risk cap, $750 daily stop, correlated NQ/ES exposure protection and cooldowns remain unchanged.
