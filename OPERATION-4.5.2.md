# OTR Market Operation 4.5.2

## Railway storage recovery + bounded raw ticks

Operation 4.5.1 exposed that the persistent Railway volume had filled from unlimited raw tick storage. 4.5.2 fixes both the boot failure and the underlying growth pattern.

### Runtime changes
- `engine.pid` now lives in `/tmp/otrmarket`, not `/app/data`.
- Strategy-engine stdout/stderr streams directly into Railway Deploy Logs.
- Engine death still terminates the dashboard so Railway restarts the full service.

### Raw quote retention
`market_quotes` is now a rolling transport/diagnostic buffer rather than an infinite tick archive.

Defaults:
- NQ: latest 50,000 raw ticks
- ES: latest 50,000 raw ticks
- GC: latest 50,000 raw ticks
- BTC: latest 10,000 raw ticks

Only already-processed NinjaTrader rows are eligible for pruning. Candles, strategy diagnostics, setups, paper trades, evaluation state and engine state are not pruned.

### Quote counters
Dashboard quote counts remain lifetime counters even after raw rows are pruned.

### Recovery note
If the Railway volume is already completely full, increase the volume capacity before deploying 4.5.2. The patch then performs a startup prune and reuses the freed SQLite pages. Do not wipe the volume unless you intentionally want to erase the existing database.
