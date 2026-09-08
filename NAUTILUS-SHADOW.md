# OTR Market NautilusTrader Shadow Integration

## Purpose

NautilusTrader is integrated as a non-authoritative replay/backtest and execution-reference engine. OTR remains the strategy brain and live execution authority during certification.

The goal is not to replace OTR execution. The goal is to run the same accepted OTR setup through two execution paths and identify exactly where they disagree.

## Safety rules

1. `OTR_NAUTILUS_SHADOW=0` is the default.
2. Nautilus is installed from `requirements-nautilus.txt`, not production `requirements.txt`.
3. Shadow failures never block OTR setups, paper fills, Sim101 commands, or live safety logic.
4. Nautilus cannot become authoritative until replay parity has been measured and explicitly promoted.
5. The dependency is pinned to `nautilus_trader==1.231.0` so results do not silently change after an upstream release.
6. The parity ledger is additive and never rewrites `strategy_setups`, `paper_trades`, or market history.

## Environment flags

- `OTR_NAUTILUS_SHADOW=1`: request the shadow runtime.
- `OTR_NAUTILUS_STRICT_PARITY=1`: retain the latest mismatch as a shadow error for diagnostics.
- `OTR_NAUTILUS_RECORD_MATCHES=1`: reserved for future always-on persistence policy.

## Phase 1: optional runtime foundation

Phase 1 added:

- optional pinned dependency;
- runtime capability probe;
- normalized execution snapshots;
- field-level parity comparison;
- fail-open bridge;
- unit tests;
- separate GitHub Actions smoke test that installs Nautilus on Python 3.12.

No live or paper execution path was changed.

Run the local probe after installing the optional dependency:

```bash
pip install -r requirements.txt
pip install -r requirements-nautilus.txt
OTR_NAUTILUS_SHADOW=1 python scripts/probe_nautilus_shadow.py
```

A healthy report must show `active: true` and the backtest, execution, risk, portfolio, and strategy capabilities as `true`.

## Phase 2: GC/MGC market replay

Phase 2 added a real Nautilus `BacktestEngine` replay path for stored OTR Gold quotes.

- The OTR strategy symbol remains normalized to `GC`.
- Exact NinjaTrader contract identity is recovered from the existing `source=ninjatrader:<contract>` field.
- GC and MGC are modeled as futures with 0.1 tick size and their correct 100x / 10x multipliers.
- Replay timestamps are deterministic even when NinjaTrader emits duplicate timestamps.
- No orders are submitted in this phase.

Manual stored-data replay:

```bash
python scripts/replay_nautilus_gc_shadow.py --limit 5000
```

An exact NinjaTrader contract can also be requested:

```bash
python scripts/replay_nautilus_gc_shadow.py --contract "MGC DEC26" --limit 5000
```

## Phase 3: bracket execution parity

Phase 3 added non-authoritative execution simulation for accepted OTR-style Gold geometry.

The Nautilus shadow submits a real simulated bracket containing:

1. limit parent entry;
2. take-profit child;
3. stop-market child.

The result is normalized into the same execution snapshot shape as OTR and includes:

- entry fill;
- exit fill;
- order states;
- result;
- R multiple;
- whole-contract GC/MGC dollar P/L.

CI includes a deterministic MGC +2R smoke test that must pass before changes are merged.

## Phase 4: offline parity ledger

Phase 4 adds the `nautilus_shadow_parity` SQLite table and an offline comparison runner.

For each recent closed OTR Gold paper trade the runner:

1. loads the persisted OTR setup and paper-trade outcome;
2. resolves the NinjaTrader Gold contract around the trade;
3. maps execution to MGC using OTR's existing execution-contract rules;
4. maps the paper risk budget to whole MGC contracts;
5. loads the retained replay ticks covering the setup through close;
6. runs the bracket through Nautilus;
7. compares OTR and Nautilus entry, state, exit, result, R, and P/L;
8. upserts one diagnostic ledger row for the setup.

Run it manually after installing the optional Nautilus dependency:

```bash
python scripts/run_nautilus_parity_ledger.py --limit 10
```

Difference categories are `IDENTITY`, `STATE`, `ENTRY`, `EXIT`, `RESULT`, `R_MULTIPLE`, `PNL`, and `OTHER`.

A P/L-only mismatch is tracked separately from trade-path parity because OTR paper risk can be an exact dollar amount while MGC execution must use whole contracts. For example, a $105 paper risk budget with a two-point MGC stop can fund five contracts and therefore exposes $100 of actual MGC risk.

## Promotion criteria

Nautilus remains shadow-only until replay runs demonstrate stable trade-path parity and every remaining mismatch has a documented explanation. Promotion to any broker-facing role requires a separate code change, separate CI coverage, and separate execution certification.
