import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import websockets
from dotenv import load_dotenv
from rich.console import Console
from rich.live import Live
from rich.table import Table

from src.storage.database import get_connection, save_quote
from src.strategies.momentum import MomentumTracker


ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

COINBASE_URL = "wss://advanced-trade-ws.coinbase.com"
ALPACA_URL = "wss://stream.data.alpaca.markets/v2/iex"

ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_API_SECRET = os.getenv("ALPACA_API_SECRET")

console = Console()
momentum = MomentumTracker()

market_state = {
    "BTC-USD": {
        "name": "Bitcoin",
        "source": "Coinbase",
        "price": None,
        "bid": None,
        "ask": None,
        "updated": None,
        "quotes": 0,
    },
    "QQQ": {
        "name": "Nasdaq",
        "source": "Alpaca IEX",
        "price": None,
        "bid": None,
        "ask": None,
        "updated": None,
        "quotes": 0,
    },
    "SPY": {
        "name": "S&P 500",
        "source": "Alpaca IEX",
        "price": None,
        "bid": None,
        "ask": None,
        "updated": None,
        "quotes": 0,
    },
}

feed_status = {
    "Coinbase": "CONNECTING",
    "Alpaca": "CONNECTING",
}


def utc_now():
    return datetime.now(timezone.utc)


def utc_iso():
    return utc_now().isoformat()


def update_market(symbol, price, bid, ask):
    market = market_state[symbol]

    market["price"] = price
    market["bid"] = bid
    market["ask"] = ask
    market["updated"] = utc_now()
    market["quotes"] += 1

    momentum.add_price(symbol, price)


def age_string(updated):
    if updated is None:
        return "WAITING"

    seconds = (utc_now() - updated).total_seconds()

    if seconds < 1:
        return f"{seconds * 1000:.0f} ms"

    return f"{seconds:.1f} s"


def price_string(value):
    if value is None:
        return "---"

    return f"${value:,.2f}"


def percent_string(value):
    if value is None:
        return "---"

    if value > 0:
        return f"+{value:.3f}%"

    return f"{value:.3f}%"


def build_dashboard():
    table = Table(
        title="OTR MARKET RADAR",
        caption="RESEARCH MODE • TRADING DISABLED 🔒",
    )

    table.add_column("Market")
    table.add_column("Price", justify="right")
    table.add_column("1s", justify="right")
    table.add_column("5s", justify="right")
    table.add_column("15s", justify="right")
    table.add_column("30s", justify="right")
    table.add_column("1m", justify="right")
    table.add_column("Age", justify="right")
    table.add_column("Quotes", justify="right")

    for symbol, market in market_state.items():
        returns = momentum.returns(symbol)

        table.add_row(
            market["name"],
            price_string(market["price"]),
            percent_string(returns["1s"]),
            percent_string(returns["5s"]),
            percent_string(returns["15s"]),
            percent_string(returns["30s"]),
            percent_string(returns["1m"]),
            age_string(market["updated"]),
            f'{market["quotes"]:,}',
        )

    table.add_section()

    table.add_row(
        "Coinbase",
        feed_status["Coinbase"],
        "",
        "",
        "",
        "",
        "",
        "",
        "",
    )

    table.add_row(
        "Alpaca",
        feed_status["Alpaca"],
        "",
        "",
        "",
        "",
        "",
        "",
        "",
    )

    return table


async def coinbase_collector(connection):
    while True:
        try:
            async with websockets.connect(
                COINBASE_URL,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=10,
            ) as websocket:

                await websocket.send(
                    json.dumps(
                        {
                            "type": "subscribe",
                            "product_ids": ["BTC-USD"],
                            "channel": "ticker",
                        }
                    )
                )

                feed_status["Coinbase"] = "CONNECTED"

                async for message in websocket:
                    data = json.loads(message)

                    if data.get("channel") != "ticker":
                        continue

                    exchange_time = data.get("timestamp")

                    for event in data.get("events", []):
                        for ticker in event.get("tickers", []):
                            if ticker.get("product_id") != "BTC-USD":
                                continue

                            price = float(ticker["price"])
                            bid = float(ticker["best_bid"])
                            ask = float(ticker["best_ask"])

                            update_market(
                                "BTC-USD",
                                price,
                                bid,
                                ask,
                            )

                            save_quote(
                                connection,
                                utc_iso(),
                                exchange_time,
                                "coinbase",
                                "BTC-USD",
                                price,
                                bid,
                                ask,
                            )

        except asyncio.CancelledError:
            raise

        except Exception as exc:
            feed_status["Coinbase"] = "RECONNECTING"
            console.log(f"Coinbase error: {exc}")
            await asyncio.sleep(5)


async def alpaca_collector(connection):
    if not ALPACA_API_KEY or not ALPACA_API_SECRET:
        feed_status["Alpaca"] = "NO CREDENTIALS"
        return

    while True:
        try:
            async with websockets.connect(
                ALPACA_URL,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=10,
            ) as websocket:

                first_message = json.loads(
                    await websocket.recv()
                )

                connected = any(
                    item.get("T") == "success"
                    and item.get("msg") == "connected"
                    for item in first_message
                )

                if not connected:
                    raise RuntimeError(
                        f"Alpaca connection failed: {first_message}"
                    )

                await websocket.send(
                    json.dumps(
                        {
                            "action": "auth",
                            "key": ALPACA_API_KEY,
                            "secret": ALPACA_API_SECRET,
                        }
                    )
                )

                auth_response = json.loads(
                    await websocket.recv()
                )

                authenticated = any(
                    item.get("T") == "success"
                    and item.get("msg") == "authenticated"
                    for item in auth_response
                )

                if not authenticated:
                    raise RuntimeError(
                        f"Alpaca authentication failed: {auth_response}"
                    )

                await websocket.send(
                    json.dumps(
                        {
                            "action": "subscribe",
                            "quotes": ["QQQ", "SPY"],
                        }
                    )
                )

                feed_status["Alpaca"] = "CONNECTED"

                async for message in websocket:
                    data = json.loads(message)

                    if not isinstance(data, list):
                        continue

                    for event in data:
                        if event.get("T") != "q":
                            continue

                        symbol = event.get("S")

                        if symbol not in ("QQQ", "SPY"):
                            continue

                        bid = float(event["bp"])
                        ask = float(event["ap"])
                        mid = (bid + ask) / 2

                        update_market(
                            symbol,
                            mid,
                            bid,
                            ask,
                        )

                        save_quote(
                            connection,
                            utc_iso(),
                            event.get("t"),
                            "alpaca_iex",
                            symbol,
                            mid,
                            bid,
                            ask,
                        )

        except asyncio.CancelledError:
            raise

        except Exception as exc:
            feed_status["Alpaca"] = "RECONNECTING"
            console.log(f"Alpaca error: {exc}")
            await asyncio.sleep(5)


async def dashboard():
    with Live(
        build_dashboard(),
        console=console,
        refresh_per_second=4,
    ) as live:
        while True:
            live.update(build_dashboard())
            await asyncio.sleep(0.25)


async def main():
    connection = get_connection()

    try:
        await asyncio.gather(
            coinbase_collector(connection),
            alpaca_collector(connection),
            dashboard(),
        )
    finally:
        connection.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())

    except KeyboardInterrupt:
        console.print(
            "\n[yellow]OTR Market stopped.[/yellow]"
        )