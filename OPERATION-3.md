# OTR Market Operation 3 — NinjaTrader Futures Bridge

Operation 3 replaces the QQQ/SPY proxy path with real futures market data delivered from NinjaTrader Desktop into OTR.

## Markets

- NQ / MNQ -> normalized internally as `NQ`
- ES / MES -> normalized internally as `ES`
- GC / MGC -> normalized internally as `GC`
- BTC-USD remains on the Coinbase public WebSocket

The strategy engine now evaluates SMT between **NQ and ES** instead of QQQ and SPY.

## Safety state

Operation 3 is still **paper/research only**. The NinjaTrader component only reads Level I market data and sends it to OTR. It does not submit, modify, or cancel orders.

## 1. Install the OTR update

From the root of the GitHub Codespace:

```bash
unzip -o otrmarket-operation-3.zip -d .
bash operation3_setup.sh
```

The setup script creates a random `OTR_BRIDGE_KEY` in `.env` if one is not already present.

Display it when needed with:

```bash
grep '^OTR_BRIDGE_KEY=' .env
```

Never commit `.env`.

## 2. Start OTR

```bash
bash run_all.sh
```

OTR exposes the bridge endpoint at:

```text
/market/api/bridge/ticks
```

## 3. Decide where the endpoint lives

### Recommended temporary development method: Codespaces

If NinjaTrader Desktop is on your Windows PC and OTR is running inside Codespaces, NinjaTrader must be able to reach the forwarded port.

1. In Codespaces, open the **Ports** tab.
2. Find port `8000`.
3. For this temporary test only, set port visibility to **Public**.
4. Set a strong `DASHBOARD_PASSWORD` in `.env` before exposing the dashboard.
5. Restart `bash run_all.sh` after editing `.env`.
6. Your bridge URL will be:

```text
https://<your-codespace>-8000.app.github.dev/market/api/bridge/ticks
```

The bridge endpoint itself also requires the independent `OTR_BRIDGE_KEY` header.

Do not leave the development port public when you are finished testing.

Later, `otrservices.com/market` should become the stable private production endpoint.

## 4. Install the NinjaTrader indicator

The source file is:

```text
ninjatrader/OTRMarketBridge.cs
```

In NinjaTrader Desktop:

1. Control Center -> **New -> NinjaScript Editor**.
2. Right-click **Indicators** -> create a new indicator named `OTRMarketBridge`.
3. Replace the generated code with the contents of `OTRMarketBridge.cs`.
4. Press **F5** to compile.
5. If NinjaTrader reports an error, copy the exact compiler message back to ChatGPT before changing random settings.

## 5. Apply the bridge

Open one chart for each market you want OTR to receive. The chart timeframe does not control OTR's candle timeframes; the bridge listens to Level I market data.

Recommended analysis feeds:

- NQ current contract
- ES current contract
- GC current contract

The bridge also accepts the micro contracts and maps them automatically:

- MNQ -> NQ
- MES -> ES
- MGC -> GC

For each chart:

1. Right-click chart -> **Indicators**.
2. Add `OTRMarketBridge`.
3. Set `Endpoint URL` to your OTR bridge URL.
4. Set `Bridge Key` to the value from `.env`.
5. Leave `Flush Interval` at 250 ms initially.
6. Apply.

Do not run both NQ and MNQ bridges at the same time for the same market, or OTR will receive duplicate streams.

## 6. Verify data arrival

On the OTR dashboard, the market cards should become:

- Nasdaq Futures / NQ
- S&P 500 Futures / ES
- Gold Futures / GC
- Bitcoin / BTC-USD

The terminal engine should also change NinjaTrader from `WAITING` to `CONNECTED`.

To inspect recent NinjaTrader rows manually:

```bash
python - <<'PY'
import sqlite3
con = sqlite3.connect('data/otrmarket.db')
for row in con.execute("""
    SELECT id, received_at, source, symbol, price, bid, ask
    FROM market_quotes
    WHERE source LIKE 'ninjatrader:%'
    ORDER BY id DESC
    LIMIT 20
"""):
    print(row)
con.close()
PY
```

## Current trial contracts seen during Operation 3 setup

The user's NinjaTrader simulation screen showed contracts including:

- NQ SEP26 / MNQ SEP26
- ES SEP26 / MES SEP26
- GC DEC26 / MGC DEC26

The bridge does not hardcode those contract months. It reads the instrument currently attached to each NinjaTrader chart, which makes rollover changes much easier.

## Data transport design

`OTRMarketBridge` listens to NinjaTrader Level I `Last` market-data events. It batches ticks locally and posts them every 250 ms by default, preserving individual trade prices inside each batch while reducing HTTP-request overhead.

The web process writes those ticks into SQLite. The OTR strategy process reads newly inserted NinjaTrader ticks, builds 1m/5m/15m/1h candles, evaluates NQ/ES SMT, and feeds the existing paper execution engine.

## Market-data privacy

Keep live exchange data private for personal testing. Do not intentionally rebroadcast a public real-time futures quote feed from the dashboard. Use dashboard authentication when the endpoint is reachable from the public internet.
