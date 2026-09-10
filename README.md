# OTR Market

OTR Market is a research-first algorithmic futures trading system. The current autonomous strategy test is **Operation 8.1**, focused on Gold (`GC` signal context with micro `MGC` execution geometry) and fed by NinjaTrader/Market Replay through the OTR bridge.

## Current runtime

- Dashboard/supervisor: `src.dashboard.server_81`
- Strategy runtime: `src.main_81`
- Active autonomous market: Gold (`GC`)
- Broker gateway: paper/shadow unless explicitly armed outside the strategy layer
- Market-data ingress: NinjaTrader bridge → `/market/api/bridge/ticks`
- Execution referee: NautilusTrader shadow parity, observational and non-authoritative
- Research layer: OTR Research Lab, evidence/counterfactual tooling only

Railway production currently overrides the container start command with `python -m src.dashboard.server_81`. `run_all.sh` and `run_dashboard.sh` intentionally launch the same supervisor for local use.

## Repository map

- `src/main_*.py` — cumulative runtime generations. Many older-looking modules are still required because later operations inherit and patch them. Do not delete these by filename age alone.
- `src/dashboard/` — authenticated dashboard, bridge APIs, supervisors, and Operation 8.1 telemetry.
- `src/strategies/` — setup detection, context, entry, continuation, reversal, and execution-quality logic.
- `src/execution/` — paper/live-gateway state and execution models.
- `src/risk/` — evaluation, sizing, geometry, and safety governors.
- `src/otr8/` — Operation 8.x candidate, regime, arbiter, pipeline, and 8.1 execution policy.
- `src/integrations/nautilus_shadow/` — read-only Nautilus execution/parity referee.
- `src/research/` — historical, replay, evidence, counterfactual, and Research Lab tooling.
- `ninjatrader/` — NinjaTrader market/execution bridge source.
- `tests/` — regression coverage for the inherited runtime and current Operation 8.1 behavior.

## Safety contract

Operation 8.1 keeps strategy authority inside OTR. Nautilus and Research Lab components may observe, replay, compare, and produce evidence, but they do not independently place trades or rewrite the active strategy. Production promotion should always pass static checks, the full regression suite, and the Nautilus smoke suite.

## Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-nautilus.txt
bash run_all.sh
```

Run the main regression suite with:

```bash
python -m unittest discover -s tests -p 'test_*.py'
```

Static hygiene used by CI:

```bash
ruff check src scripts --select E9,F401,F811,F821,F841
python -m compileall -q src scripts
```

Nautilus-specific architecture and certification notes are in `NAUTILUS-SHADOW.md` and `NAUTILUS-AUTO-CERTIFY.md`. The current execution policy is documented in `OPERATION-8.1.md`; older `OPERATION-*` files are retained as historical design records and can reference bootstrap scripts that no longer belong in the active repository root.
