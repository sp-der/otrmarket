# OTR Market Operation 8.1 - Railway Hosting

OTR Market production runs the authenticated FastAPI dashboard/supervisor, NinjaTrader bridge ingress, Operation 8.1 Gold engine, SQLite state, Research Lab surfaces, and Nautilus shadow diagnostics on Railway.

## Production endpoints

- Dashboard: `https://market.otrservicesie.com/market/`
- NinjaTrader bridge: `https://market.otrservicesie.com/market/api/bridge/ticks`
- Health: `https://market.otrservicesie.com/market/api/health`
- Nautilus parity: `https://market.otrservicesie.com/market/nautilus-parity`

## Service source and runtime

Railway deploys `sp-der/otrmarket` from `main` with the repository `Dockerfile` and `railway.json`.

The production service currently uses an explicit start command:

```text
python -m src.dashboard.server_81
```

`run_all.sh` and `run_dashboard.sh` intentionally target the same supervisor for local use.

## Persistent data

Mount the Railway volume at:

```text
/app/data
```

This volume holds the SQLite trading/research state. Do not replace or wipe it during routine code deployments. Operation 8.1's replay-reset marker is idempotent and should only change when a new reset is deliberately approved.

## Required private variables

Keep secrets in Railway Variables, never in GitHub:

```text
OTR_BRIDGE_KEY=<private bridge key>
DASHBOARD_PASSWORD=<private dashboard password>
DASHBOARD_SESSION_SECRET=<long random secret>
DASHBOARD_SECURE_COOKIE=1
DASHBOARD_HOST=0.0.0.0
OTR_ENGINE_MODULE=src.main_81
```

Railway injects `PORT` automatically. Do not commit secret values or manually hard-code a production port into the application.

Execution safety should remain paper/shadow unless live execution is deliberately certified and armed outside the strategy layer. The safe local defaults are documented in `.env.example`.

## Deployment verification

After a deployment, verify all of the following before using a replay as evidence:

1. Railway deployment status is `SUCCESS`.
2. `/market/api/health` returns healthy status.
3. Supervisor boot reports `src.main_81` / Operation 8.1.
4. Trading focus remains Gold and broker gateway remains in the intended PAPER/shadow state.
5. NinjaTrader bridge POSTs return HTTP 200 while replay data is flowing.
6. Nautilus smoke/parity tooling remains non-authoritative.

## NinjaTrader bridge

Every installed `OTRMarketBridge` instance should point at:

```text
https://market.otrservicesie.com/market/api/bridge/ticks
```

Use the same private bridge key configured in Railway. GitHub/Railway code changes do not automatically replace or recompile the locally installed NinjaScript bridge.

## Optional proxy deployment

`deploy/cloudflare-worker-market-proxy.js` and `deploy/nginx-market.conf.example` are retained as optional deployment references. They are not part of the normal Railway production path.
