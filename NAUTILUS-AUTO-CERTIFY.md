# Nautilus Auto-Certification

OTR Market Operation 8.1 now certifies newly closed Gold paper trades automatically while their raw NinjaTrader tick window is still fresh.

## Runtime boundary

The auto-certifier runs in the dashboard/research process, not in the strategy engine. It is non-authoritative and fail-open.

It may:

- read closed `GC` rows from `paper_trades` and matching `strategy_setups`
- replay those trades through the existing Nautilus shadow parity engine
- write permanent results to `nautilus_shadow_parity`
- write its own job state to `nautilus_shadow_auto_jobs`

It may not:

- alter strategy rules
- approve or reject a setup
- change risk
- place or modify broker orders
- modify `paper_trades`, `strategy_setups`, market history, or Operation 8.1 policy

## Flow

```text
Gold trade closes
      |
      v
paper_trades persists CLOSED row
      |
      v
Research/dashboard auto-certifier (2s poll)
      |
      v
Nautilus shadow replays retained post-setup ticks
      |
      v
nautilus_shadow_parity stores permanent certification
      |
      v
OTR Research Lab displays MATCH / DIFF on the trade
```

The worker always claims the newest uncertified closed Gold trade first. This keeps a fresh replay trade from sitting behind stale historical jobs.

## Retry behavior

A trade receives at most 3 attempts.

- `WAITING_TICKS`: not enough ticks yet, wait before retrying
- `RETRY`: temporary non-retention diagnostic failure
- `CERTIFIED`: Nautilus result stored successfully
- `UNAVAILABLE_RETENTION`: all attempts exhausted because the raw tick window is already gone
- `ERROR`: a different failure exhausted its retry budget

Retries yield for the configured poll interval so the bridge can persist more ticks before the next attempt.

If Railway restarts while a job is `RUNNING`, startup requeues that interrupted job as `RETRY` without touching the trade.

## Startup behavior

Automatic startup is enabled by default on Railway because Railway injects `RAILWAY_ENVIRONMENT` / `RAILWAY_PROJECT_ID`.

Local development and tests do not start the daemon by default. They can opt in with:

```text
OTR_NAUTILUS_AUTO_CERTIFY=1
```

It can be disabled explicitly with:

```text
OTR_NAUTILUS_AUTO_CERTIFY=0
```

Polling defaults to 2 seconds and may be adjusted with:

```text
OTR_NAUTILUS_AUTO_POLL_SECONDS=2
```

## Research Lab visibility

`/market/research-lab` shows an `Auto Certifier` card with worker state, certified count, and old trades that could not be reconstructed because retention had already expired.

The Aug 31 through Sep 7 Baseline #1 remains valid research evidence, but its seven trades remain execution-uncertified because their raw tick windows had already expired before this feature was deployed. Future closed Gold trades are automatically attempted near close time.
