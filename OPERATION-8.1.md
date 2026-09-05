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

## Validation goal

Measure the full funnel:

`candidate -> qualified -> arbiter selected -> execution zone armed -> pending registered -> filled/open -> closed`

Track failures separately, especially `QUALITY_BLOCKED`, `ARBITER_BLOCKED`, `MISSED_EXTENDED`, `EXPIRED_BEFORE_ENTRY`, `RISK_REJECTED` and account-guard blocks.
