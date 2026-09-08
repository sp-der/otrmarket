# OTR Market NautilusTrader Shadow Integration

## Purpose

NautilusTrader is being introduced as a non-authoritative replay/backtest and execution-reference engine. OTR remains the strategy brain and live execution authority during certification.

The first goal is not to replace OTR execution. The goal is to run the same accepted OTR setup through two execution paths and identify exactly where they disagree.

## Safety rules

1. `OTR_NAUTILUS_SHADOW=0` is the default.
2. Nautilus is installed from `requirements-nautilus.txt`, not production `requirements.txt`.
3. Shadow failures never block OTR setups, paper fills, Sim101 commands, or live safety logic.
4. Nautilus cannot become authoritative until replay parity has been measured and explicitly promoted.
5. The dependency is pinned to `nautilus_trader==1.231.0` so results do not silently change after an upstream release.

## Environment flags

- `OTR_NAUTILUS_SHADOW=1`: request the shadow runtime.
- `OTR_NAUTILUS_STRICT_PARITY=1`: retain the latest mismatch as a shadow error for diagnostics.
- `OTR_NAUTILUS_RECORD_MATCHES=1`: reserved for persisting successful parity comparisons as well as mismatches.

## Phase 1

Phase 1 adds:

- optional pinned dependency;
- runtime capability probe;
- normalized execution snapshots;
- field-level parity comparison;
- fail-open bridge;
- unit tests;
- separate GitHub Actions smoke test that installs Nautilus on Python 3.12.

No live or paper execution path is changed in Phase 1.

Run the local probe after installing the optional dependency:

```bash
pip install -r requirements.txt
pip install -r requirements-nautilus.txt
OTR_NAUTILUS_SHADOW=1 python scripts/probe_nautilus_shadow.py
```

A healthy report must show `active: true` and the backtest, execution, risk, portfolio, and strategy capabilities as `true`.

## Phase 2: GC replay shadow

After Phase 1 CI passes:

1. Subscribe the shadow adapter to the same GC/MGC replay clock and market events used by OTR.
2. Convert qualified `StrategySetup` objects into Nautilus order intents.
3. Keep OTR's setup grading, ICT logic, eval guard, risk sizing, and no-chase rules upstream.
4. Compare requested entry, actual fill state, exit, result R, and result dollars.
5. Persist mismatches with a category such as `ENTRY`, `TIMING`, `FILL`, `STOP`, `TARGET`, `STATE`, or `PNL`.
6. Surface mismatch counts and recent divergence reasons in the OTR dashboard.

## Promotion criteria

Nautilus must remain shadow-only until replay runs demonstrate stable parity and every remaining mismatch has a documented explanation. Promotion to any broker-facing role requires a separate change and separate certification.
