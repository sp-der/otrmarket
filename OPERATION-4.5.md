# OTR Market Operation 4.5

## Risk Geometry + Prop Evaluation Guard

Operation 4.5 fixes invalid bearish trade geometry and adds the first prop-account training governor.

### Trade geometry hardening

- Futures prices are rounded to executable tick sizes before a setup is accepted.
- NQ / ES use 0.25-point ticks; GC uses 0.10-point ticks.
- Long setups must satisfy `stop < entry < target`.
- Short setups must satisfy `target < entry < stop`.
- A liquidity-sweep level is only used as a stop anchor if it is on the correct protective side of entry.
- PaperExecutor independently rejects invalid geometry as a second safety layer.
- Risk rejection reasons are surfaced in scanner diagnostics.

### Prop evaluation training guard

The default profile models a 50K evaluation with:

- starting balance: $50,000
- profit target: $3,000
- modeled max loss limit: $2,000
- modeled firm daily loss limit: $1,200
- EOD trail threshold: $52,100
- locked MLL floor: $50,100
- maximum profile size metadata: 40 micros

OTR intentionally uses stricter internal training limits by default:

- $100 base paper risk per trade
- $400 internal daily stop
- $400 safety reserve above the modeled MLL floor
- 4 filled trades per day maximum
- 3 consecutive losses maximum
- 1 pending/open position at a time
- no new entries from 4:30 PM to 6:00 PM ET

These are OTR risk-training settings, not firm rules. They are configurable through environment variables.

### Evaluation dashboard

Overview now includes an Evaluation Guard panel showing:

- modeled paper balance
- progress toward $3,000 target
- current EOD MLL floor and cushion
- today's dollar P/L
- available risk for the next trade
- committed pending/open risk
- trades today and loss streak
- guard status / reason

Only trades created under Operation 4.5 with `risk_dollars` are included in the evaluation training ledger. Older research trades remain in the journal but are not counted against the new evaluation simulation.

### Safety

Operation 4.5 remains paper-only. It does not send any orders to NinjaTrader.

## Dollar P/L dashboard

- Adds a realized **P/L** column next to **R** in both trade tables.
- Positive realized P/L is green; losses are red.
- Replaces Profit Factor / Max Drawdown headline cards with **All-Time P/L** and **Today's P/L**.
- Daily P/L follows the active replay/trading day in America/New_York rather than wall-clock date.
- Legacy Operation 1-4.4 trades did not store dollar risk, so dashboard-only historical P/L is normalized using the configured base evaluation risk (default $100 per 1R). Those legacy rows remain excluded from the new evaluation-risk ledger.
- New Operation 4.5 trades use their exact modeled `result_dollars`, including reduced risk when the Evaluation Guard throttles a trade.
