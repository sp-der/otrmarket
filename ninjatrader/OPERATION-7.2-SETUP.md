# NinjaTrader Operation 7.2 SIM Certification

`OTRExecutionBridge.cs` is the phase-1 broker adapter for OTR Market. It is intentionally separate from `OTRMarketBridge.cs`: the market-data indicator has no order authority, while the execution indicator is SIM-only and explicitly armed.

## Current safety limits

- `ArmSimulationOrders` defaults to `false`.
- Account name must begin with `Sim` locally, even if the server is misconfigured.
- Phase 1 accepts exactly **1 micro contract** per OTR command.
- OTR server must independently be in `SIM_BRIDGE`, armed, on the same account, and freshly reconciled.
- The server remains `PAPER` + disarmed by default after deployment.
- Duplicate server delivery is ignored using the deterministic OTR `command_id` embedded in NinjaTrader order names.
- Entry fills create an OCO target + protective stop from OTR's approved prices.
- Broker/order state is posted back to OTR and the server blocks new delivery if account reconciliation fails.

## Install when at the trading PC

1. In NinjaTrader, open **New → NinjaScript Editor**.
2. Ensure the NinjaScript references include **System.Web.Extensions**. `OTRExecutionBridge.cs` uses `System.Web.Script.Serialization.JavaScriptSerializer` so it does not ship a third-party JSON DLL.
3. Add/import `OTRExecutionBridge.cs` as an Indicator and compile it.
4. Add the indicator to one realtime chart. It does not depend on that chart's instrument for execution.
5. Set:
   - `Execution API Base URL` to the OTR server path ending in `/market/api/bridge/execution`
   - `Bridge Key` to the same private key used by `OTRMarketBridge`
   - `Account` to `Sim101`
   - leave `ARM SIM ORDERS = false`
6. Confirm the OTR dashboard **System → Execution Safety** panel sees a bridge heartbeat and a clean flat-account reconciliation.
7. Only during supervised certification, change Railway to `OTR_EXECUTION_MODE=SIM_BRIDGE`, `OTR_EXECUTION_ARMED=1`, keep `OTR_EXECUTION_MAX_MICROS=1`, and then set `ARM SIM ORDERS = true` in NinjaTrader.

## Certification sequence

Do not increase size until every item passes:

- Flat Sim101 snapshot reconciles.
- One approved OTR setup produces exactly one NinjaTrader entry order.
- Server retry/redelivery does not duplicate that order.
- Entry fill creates exactly one OCO target and one protective stop.
- Target fill closes OTR command and cancels stop through OCO.
- Stop fill closes OTR command and cancels target through OCO.
- Entry rejection is reported and blocks further execution for review.
- NinjaTrader restart with a working OTR order does not duplicate it.
- Railway restart restores command state from the persistent database.
- Unexpected manual Sim101 position causes reconciliation to fail and blocks new commands.
- Dashboard kill switch immediately blocks new command delivery.

## Phase-1 limitation

Partial-fill management above one contract is intentionally not implemented. The NinjaTrader adapter rejects any OTR command whose quantity is not exactly 1. Multi-contract sizing should only be enabled after a later adapter revision can resize protective brackets correctly as partial fills arrive.
