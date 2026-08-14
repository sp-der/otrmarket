# OTR Market - Operation 2: Web Control Center

Operation 2 adds a real web dashboard on top of the Operation 1 strategy engine.

## What this operation adds

- Sleek responsive dashboard at `/market`
- Live WebSocket refresh from the SQLite trading database
- BTC, Nasdaq (QQQ), and S&P 500 (SPY) market cards
- 1-minute and 5-minute movement calculated from stored quotes
- Paper-trading KPIs: total R, win rate, average R, profit factor, max drawdown, today R
- Equity curve built from closed paper trades
- Pending/open/invalidated trade counts
- Full paper trade journal with filters
- Full strategy setup feed with trigger, direction, entry, stop, target, and R:R
- Database and candle-engine health view
- Optional dashboard password protection
- Docker deployment files
- Nginx path proxy example for `otrservices.com/market`
- Optional Cloudflare Worker path proxy example

## Install

From the root of the `otrmarket` repo:

```bash
unzip -o otrmarket-operation-2-dashboard.zip -d .
bash operation2_setup.sh
```

## Run in Codespaces

Dashboard only:

```bash
bash run_dashboard.sh
```

Engine + dashboard together:

```bash
bash run_all.sh
```

Open port `8000`, then visit `/market` on the forwarded URL.

## Optional password

Add to `.env`:

```env
DASHBOARD_PASSWORD=choose-a-strong-password
DASHBOARD_SESSION_SECRET=choose-a-long-random-secret
```

For HTTPS production deployment also set:

```env
DASHBOARD_SECURE_COOKIE=1
```

Do not commit `.env`.

## Production path

The app itself is intentionally built around these paths:

- Dashboard: `/market`
- API: `/market/api/snapshot`
- WebSocket: `/market/ws`

That means it can sit behind `otrservices.com/market` without rebuilding the frontend.

The included `deploy/nginx-market.conf.example` is for a server where Nginx already controls `otrservices.com`.

If the main site and the trading server live on different origins, the included `deploy/cloudflare-worker-market-proxy.js` can be used as a path proxy after setting its `MARKET_ORIGIN` variable.

## Security

Operation 2 still has no live-order controls. It reads OTR's research/paper database only.

If the dashboard will be internet-accessible, set `DASHBOARD_PASSWORD` before exposing it publicly.
