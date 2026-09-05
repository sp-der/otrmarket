# Operation 8.1 — Gold Execution Conversion

Operation 8.1 keeps the Operation 8.0 decision architecture and focuses on the gap between a qualified/selected Gold setup and an actual paper fill.

## Approved evaluation-training contract

- Gold only for autonomous strategy testing.
- A+/6-of-6: up to **$750** risk.
- A/strong 5-of-6: up to **$500** risk.
- 4-of-6: chart preview only, never executable.
- Lower-quality grades remain research-only.
- Internal daily drawdown stop: **$1,000**.
- Session objective: **$1,500** for measurement only.
- No maximum number of trades.
- No session profit ceiling.
- Evaluation target can continue to be measured after it is reached.

## Execution conversion

### First-touch zones

OTR already generated FVG, OTE and order-block entry alternatives, but the legacy executor ultimately waited for one scalar `entry_price`. 8.1 converts the chosen geometry into a valid execution zone and uses the least-favorable edge that still preserves the required R:R as the first-touch activation price.

This keeps fills conservative while removing the requirement to tag one exact midpoint.

### Pending lifetime

Strategy/thesis age and order-book age are separate clocks.

A strategy can spend time proving the thesis before approval. Once approved, the pending-entry lifetime begins at registration:

- 1m: 12 bars
- 5m: 8 bars
- 15m: 5 bars
- 1h: 3 bars

Structural invalidation and the existing no-chase protections remain authoritative.

## Dynamic R:R

The universal 1.50R wall is replaced for A/A+ Gold setups:

- A+: deterministic floor starts at 1.20R.
- A: deterministic floor starts at 1.30R.
- CHOP, WARMUP or regime-opposed candidates tighten back toward 1.50R.
- The specialized rejection-block lane retains its existing 3.00R rule.
- Same-strategy/timeframe/regime counterfactual history can influence the A-tier floor only after at least 20 resolved samples.
- Non-positive counterfactual expectancy tightens the floor; positive evidence cannot bypass setup quality, context, regime, exposure or cooldown gates.

## Safeguards retained

- Gold regime engine and higher-timeframe context.
- Setup Arbiter 8.0.
- Session/market-open checks.
- One active/pending idea per symbol.
- Same-symbol and global post-loss cooldowns.
- Gold 1m reversal/firewall checks.
- Geometry validation.
- 75% pre-entry target-progress no-chase protection.
- Counterfactual rejected-setup tracking.
- Decision traces.
- Evaluation account loss limits.

## Hidden quota removed

The old two-per-day continuation re-arm quota is not authoritative in 8.1. Continuations still need fresh displacement, higher-timeframe support, Gold-regime compatibility, exposure clearance, cooldown clearance and the normal account guard.

## Conversion funnel instrumentation

Operation 8.1 measures the real execution chain from `decision_traces_80` and joins each selected idea to its current `paper_trades` state:

`detected -> qualified -> arbiter selected -> pending registered -> filled/open -> closed`

The Overview Gold Decision Funnel now shows those stages directly, plus conversion percentages by timeframe and terminal drop-off buckets.

The protected endpoint is:

`GET /market/api/otr81/conversion`

It reports the active Gold session when one is in progress, otherwise the current replay trading day. It distinguishes, among others:

- `rr_blocked`
- `context_blocked`
- `quality_blocked`
- `arbiter_blocked`
- `guard_blocked`
- `risk_rejected`
- `missed_extended`
- `expired_entry`
- `waiting_entry`

Because `paper_trades` is joined at read time, a decision originally traced as `PENDING` later moves naturally into filled, closed, missed, or expired statistics without rewriting historical decision traces.

## Validation goal

Use the conversion funnel to prove that 8.1 improves selected-to-fill conversion without sacrificing expectancy or drawdown control. Compare:

- detected-to-qualified rate
- qualified-to-selected rate
- selected-to-registered rate
- registered-to-fill rate
- selected-to-fill rate
- win/loss outcome after fill
- drop-offs by timeframe and strategy
- session P/L, average R, profit factor and maximum drawdown

## Promotion status

Staging promotion gate: **76/76 tests passing** with `src.main_81` booting in EVAL mode and the conversion endpoint registered. Production promotion is a fast-forward from the tested Work branch so the tested commit set, not a rebuilt patch stack, becomes authoritative.
