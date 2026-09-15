# OTR Vibe Research Sidecar

OTR Market uses Vibe-Trading as a **non-authoritative research scientist**, not as a trading engine.

Pinned upstream package: `vibe-trading-ai==0.1.15` (MIT, Beta).

## Why it is isolated

Both OTR Market and Vibe-Trading expose a top-level Python package named `src`. Installing Vibe into OTR's main Python environment would create an unsafe namespace collision.

Production therefore keeps two environments inside the same Railway container:

- OTR + Nautilus: normal application Python environment
- Vibe-Trading: isolated virtual environment at `/opt/vibe`

The OTR dashboard invokes `/opt/vibe/bin/vibe-trading` as a subprocess. Vibe never imports into the OTR strategy process.

## Replay lifecycle

```text
NinjaTrader replay
        ↓
OTR Operation 8.1
        ↓
closed Gold trade
        ↓
Nautilus auto-certifier
        ↓
execution verdict / terminal diagnostic state
        ↓
OTR read-only research packet
        ↓
Vibe-Trading research agent
        ↓
hypotheses + replay experiments + evidence gaps
```

Nautilus runs first so Vibe can distinguish execution disagreement from strategy/market behavior.

## Authority boundary

Vibe has no authority to:

- place or cancel orders
- connect to NinjaTrader or a broker
- change Operation 8.1
- change risk limits
- change evaluation settings
- promote a hypothesis into production

Vibe receives a bounded research packet after a Gold trade closes. Its prompt permits only `HOLD`, `COLLECT_MORE`, or `TEST_IN_REPLAY` as promotion recommendations.

The subprocess receives an explicit environment allowlist. OTR bridge keys, dashboard credentials, session secrets and execution credentials are not passed to Vibe. Shell tools are disabled with `VIBE_TRADING_ENABLE_SHELL_TOOLS=0`.

## Persistence

The integration adds only research tables:

- `vibe_research_jobs_v02`
- `vibe_research_findings_v02`

Packets and outputs are kept on the persistent Railway volume under:

`/app/data/vibe-research/`

Strategy setups, paper trades, decision traces and broker state are read-only inputs to this integration.

## Provider states

The worker can collect packets before an LLM provider is configured.

- `WAITING_PROVIDER`: evidence packet has been preserved but AI analysis has not run yet
- `PACKAGE_UNAVAILABLE`: isolated Vibe executable is missing
- `RUNNING`: Vibe is analyzing the packet
- `COMPLETE`: finding is persisted
- `RETRY`: a bounded run failed or timed out and can be retried
- `ERROR`: terminal research-sidecar error

Once a supported unattended provider is configured, preserved `WAITING_PROVIDER` jobs are eligible for analysis automatically.

`openai-codex` interactive OAuth is intentionally not treated as an unattended Railway provider. Use an API-key-backed provider for automatic replay research.

## Configuration

```text
OTR_VIBE_RESEARCH=1
OTR_VIBE_PROVIDER=<provider>
OTR_VIBE_MODEL=<model>
OTR_VIBE_POLL_SECONDS=3
OTR_VIBE_MAX_ITER=12
OTR_VIBE_TIMEOUT_SECONDS=180
```

Provider credentials must be configured privately in Railway and must never be committed to the repository or pasted into research packets.

## Protected surfaces

- Page: `/market/vibe-research`
- API: `/market/api/vibe-research`

Both use the same dashboard authentication boundary as the rest of OTR's protected diagnostics.

## Promotion contract

Vibe findings are evidence, not instructions. Any future strategy change must still pass:

`finding → hypothesis → replay experiment → evidence threshold → Nautilus validation → feature branch → CI → replay certification → human approval → production`
