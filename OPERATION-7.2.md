# OTR Market Operation 7.2 — Fail-Closed Execution Kernel

Operation 7.2 is the bridge between the existing OTR decision engine and a future NinjaTrader broker executor. It does not change the trading strategy. It mirrors only setups that already passed the existing session, quality, geometry, cooldown and evaluation-risk gates.

## Safety posture

Production defaults to:

```text
OTR_EXECUTION_MODE=PAPER
OTR_EXECUTION_ARMED=0
OTR_EXECUTION_ACCOUNT=Sim101
OTR_EXECUTION_LIVE_ALLOWED=0
OTR_EXECUTION_CERTIFIED=0
```

In this state the new code can audit approved setups but cannot emit broker commands.

The first executable certification profile is:

```text
OTR_EXECUTION_MODE=SIM_BRIDGE
OTR_EXECUTION_ARMED=1
OTR_EXECUTION_ACCOUNT=Sim101
OTR_EXECUTION_MAX_MICROS=1
```

Even in SIM_BRIDGE, command delivery stays blocked until NinjaTrader has posted a fresh broker snapshot that reconciles with OTR state.

LIVE mode has two additional independent interlocks:

```text
OTR_EXECUTION_LIVE_ALLOWED=1
OTR_EXECUTION_CERTIFIED=1
```

Do not enable those during SIM certification.

## Architecture

```text
NinjaTrader market data
        |
        v
OTR Market Intelligence 1.0
        |
session / quality / geometry / eval risk
        |
        v
PaperExecutor (unchanged research ledger)
        |
        +--> Operation 7.2 approved-setup mirror
                 |
                 v
          execution safety kernel
                 |
          idempotent SQLite queue
                 |
         fresh reconciliation required
                 |
                 v
       NinjaTrader execution bridge
                 |
         ACK / WORKING / FILL / CLOSE
                 |
                 v
          execution event ledger
```

The `setup_id` is unique in `execution_commands`, and each broker command gets a deterministic `command_id`. Broker events require their own `event_id`. These keys are the first duplicate-order defense across retries and restarts.

## Bridge protocol

All bridge routes use the existing `X-OTR-Bridge-Key`.

### POST `/market/api/bridge/execution/snapshot`

NinjaTrader sends current broker truth before any command can be delivered.

```json
{
  "bridge_id": "desktop-main",
  "timestamp": "2026-08-28T20:00:00Z",
  "account": "Sim101",
  "positions": [],
  "orders": []
}
```

If OTR and broker position/order state disagree, reconciliation becomes false and new command delivery fails closed.

### GET `/market/api/bridge/execution/commands`

Returns no commands unless all execution interlocks pass. Approved commands are short-lived and include deterministic command/setup IDs, account/mode, micro execution contract, side/quantity, limit entry, stop, target, dollar risk, quality context and expiry.

The NinjaTrader side must treat `command_id` as an idempotency key.

### POST `/market/api/bridge/execution/events`

NinjaTrader reports lifecycle events:

```text
ACKNOWLEDGED
SUBMITTED
WORKING
PARTIAL
FILLED
CLOSED
CANCELLED
REJECTED
```

Each event has a unique `event_id` so retrying an HTTP request cannot duplicate the event in OTR.

## Dashboard-safe controls

`GET /market/api/execution/status` is protected by normal dashboard auth and shows mode, account, arming state, reconciliation, bridge heartbeat, queue counts and the latest execution audit.

`POST /market/api/execution/kill-switch` is also dashboard-authenticated. The kill switch is sticky in the OTR database and blocks new broker delivery even if environment variables are otherwise armed.

There is intentionally no dashboard endpoint that can arm live trading. Arming remains a deployment/environment decision.

## What remains for supervised PC work

1. Compile/install the NinjaTrader execution adapter.
2. Confirm the exact contract-name normalization produced by the connected NinjaTrader feed.
3. Post a flat Sim101 reconciliation snapshot and verify `reconciled=true`.
4. Set SIM_BRIDGE + `OTR_EXECUTION_ARMED=1` with `MAX_MICROS=1`.
5. Run a controlled order lifecycle certification: entry, acknowledgement, fill, stop/target, cancel, restart, disconnect and duplicate-command tests.
6. Only after that certification should sizing or trade-management behavior be expanded.

Operation 7.2 deliberately separates “strategy profitable?” from “robot safe and reliable?”. The latter must pass first.
