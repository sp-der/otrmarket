# OTR Market NautilusTrader Shadow Integration

## Purpose

NautilusTrader is integrated as a non-authoritative replay/backtest and execution-reference engine. OTR remains the strategy brain and live execution authority during certification.

The goal is not to replace OTR execution. The goal is to run the same accepted OTR setup through two execution paths and identify exactly where they disagree.

## Safety rules

1. `OTR_NAUTILUS_SHADOW=0` is the default.
2. Nautilus is pinned separately in `requirements-nautilus.txt`; the production image also installs that pinned diagnostic runtime so the authenticated parity page can operate against Railway's persistent replay database.
3. Shadow failures never block OTR setups, paper fills, Sim101 commands, or live safety logic.
4. Nautilus cannot become authoritative until replay parity has been measured and explicitly promoted.
5. The dependency is pinned to `nautilus_trader==1.231.0` so results do not silently change after an upstream release.
6. The parity ledger is additive and never rewrites `strategy_setups`, `paper_trades`, or market history.
7. Installing the Nautilus package does not connect it to NinjaTrader or OTR's broker gateway. The production parity surface only reads persisted trades/quotes and writes diagnostic ledger rows.

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
2. resolves the NinjaTrader Gold contract at the time the setup was created;
3. maps execution to MGC using OTR's existing execution-contract rules;
4. maps the paper risk budget to whole MGC contracts;
5. loads retained replay ticks beginning at the setup creation time, never before it;
6. runs the bracket through Nautilus;
7. compares OTR and Nautilus entry, state, exit, result, R, and P/L;
8. upserts one diagnostic ledger row for the setup.

The runner intentionally never feeds Nautilus quotes from before `strategy_setups.created_at`. This prevents a shadow limit order from filling before OTR had actually generated the setup.

Run it manually after installing the optional Nautilus dependency:

```bash
python scripts/run_nautilus_parity_ledger.py --limit 10
```

The JSON result reports `requested`, successful `records`, trade-path/full-match counts, detailed mismatches, and `skipped` trades. A trade is skipped rather than aborting the batch when its required raw ticks have already rolled outside OTR's quote-retention window.

Difference categories are `IDENTITY`, `STATE`, `ENTRY`, `EXIT`, `RESULT`, `R_MULTIPLE`, `PNL`, and `OTHER`.

A P/L-only mismatch is tracked separately from trade-path parity because OTR paper risk can be an exact dollar amount while MGC execution must use whole contracts. For example, a $105 paper risk budget with a two-point MGC stop can fund five contracts and therefore exposes $100 of actual MGC risk.

### Replay workflow

For the next Gold replay, run OTR normally. The Nautilus integration does not change setup recognition, trade approval, NinjaTrader execution, stop/target management, or eval controls. After replay, run the parity ledger while the relevant raw GC ticks are still retained.

On Railway production, sign into the normal OTR Market dashboard and open:

```text
/market/nautilus-parity
```

The page uses the existing dashboard session and exposes two diagnostic actions only: refresh the persisted ledger and run parity for the most recent 1-25 closed Gold trades. The run is executed in FastAPI's worker thread rather than the async dashboard loop.

For local/dev usage the equivalent command remains:

```bash
python scripts/run_nautilus_parity_ledger.py --limit 10
```

Start with the most recent 10 closed Gold trades. If a longer replay produces more trades than the retained raw quote window can cover, the runner will preserve the comparisons it can make and explicitly list older trades it skipped.

## CI certification

The Nautilus CI lane verifies all four layers:

1. optional Nautilus runtime imports;
2. synthetic MGC market replay;
3. synthetic MGC bracket execution;
4. an end-to-end in-memory OTR database → GC contract recovery → MGC bracket → parity-ledger full match.

The normal regression lane also verifies the Operation 8.1 parity dashboard surface, persisted summary projection, route idempotency, and its explicit shadow-only labeling without requiring Nautilus to import during ordinary application startup.

## Promotion criteria

Nautilus remains shadow-only until replay runs demonstrate stable trade-path parity and every remaining mismatch has a documented explanation. Promotion to any broker-facing role requires a separate code change, separate CI coverage, and separate execution certification.
