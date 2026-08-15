# OTR Market Operation 4.3 - Permanent Railway Hosting

This deployment keeps the FastAPI dashboard, WebSocket endpoint, NinjaTrader
bridge ingress, strategy engine, and SQLite database running outside GitHub
Codespaces.

## Target URLs

Recommended production hostname:

- Dashboard: https://market.otrservices.com/market/
- NinjaTrader bridge: https://market.otrservices.com/market/api/bridge/ticks
- Health: https://market.otrservices.com/market/api/health

The root otrservices.com domain can remain available for a future public site.

## 1. Deploy the GitHub repo to Railway

Create a Railway project and deploy the GitHub repository:

    sp-der/otrmarket

Railway will detect the Dockerfile and railway.json.

## 2. Add a persistent volume

Attach a volume to the OTR service with mount path:

    /app/data

This is required because OTR currently stores quotes, candles, diagnostics,
setups, paper trades, and engine state in SQLite at ./data/otrmarket.db.

## 3. Add service variables

Copy the values privately from your local .env into Railway Variables. Never
commit these values to GitHub.

Required/recommended:

    OTR_BRIDGE_KEY=<existing private bridge key>
    DASHBOARD_PASSWORD=<choose a production dashboard password>
    DASHBOARD_SESSION_SECRET=<long random secret>
    DASHBOARD_SECURE_COOKIE=1
    DASHBOARD_HOST=0.0.0.0

Railway injects PORT automatically; do not set PORT manually.

ALPACA_API_KEY and ALPACA_API_SECRET are legacy/optional for the current
NinjaTrader futures path.

## 4. Generate the temporary Railway domain

In Railway service Settings -> Networking -> Public Networking, generate a
Railway domain. Test:

    https://<railway-domain>/market/api/health

Expected JSON includes:

    "ok": true

## 5. Add market.otrservices.com

In Railway service Settings -> Networking -> Custom Domain, add:

    market.otrservices.com

Railway will show the exact CNAME and TXT verification records. Add both in
Cloudflare DNS exactly as Railway provides them. Do not guess the target.

After Railway marks the domain verified, test:

    https://market.otrservices.com/market/api/health

## 6. Update NinjaTrader once

On every OTRMarketBridge instance, replace the temporary Codespaces endpoint
with:

    https://market.otrservices.com/market/api/bridge/ticks

Keep the same OTR_BRIDGE_KEY value that was copied into Railway.

After this change, deleting/recreating Codespaces will not change the bridge
URL.

## Optional: otrservices.com/market

If you later want the dashboard at https://otrservices.com/market/ as well,
use the existing deploy/cloudflare-worker-market-proxy.js Worker and set its
MARKET_ORIGIN variable to https://market.otrservices.com. This is optional;
NinjaTrader should use the direct market.otrservices.com endpoint.
