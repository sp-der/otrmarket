import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import websockets
from rich.console import Console
from rich.live import Live
from rich.table import Table

from src.execution.paper import PaperExecutor
from src.storage.database import (
    get_connection,
    save_candle,
    save_quote,
    save_setup,
    upsert_paper_trade,
)
from src.strategies.candles import CandleBuilder
from src.strategies.confluence import ConfluenceEngine
from src.strategies.momentum import MomentumTracker

ROOT = Path(__file__).resolve().parents[1]
COINBASE_URL = "wss://advanced-trade-ws.coinbase.com"

console = Console()
momentum = MomentumTracker()
candles = CandleBuilder(timeframes=("1m", "5m", "15m", "1h"))
strategy = ConfluenceEngine()
paper = PaperExecutor()

market_state = {
    "BTC-USD": {"name": "Bitcoin", "source": "Coinbase", "price": None, "bid": None, "ask": None, "updated": None, "quotes": 0},
    "NQ": {"name": "Nasdaq Futures", "source": "NinjaTrader", "price": None, "bid": None, "ask": None, "updated": None, "quotes": 0},
    "ES": {"name": "S&P 500 Futures", "source": "NinjaTrader", "price": None, "bid": None, "ask": None, "updated": None, "quotes": 0},
    "GC": {"name": "Gold Futures", "source": "NinjaTrader", "price": None, "bid": None, "ask": None, "updated": None, "quotes": 0},
}

feed_status = {
    "Coinbase": "CONNECTING",
    "NinjaTrader": "WAITING",
}


def utc_now():
    return datetime.now(timezone.utc)


def parse_timestamp(value: str | None) -> datetime:
    if not value:
        return utc_now()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return utc_now()


def price_string(value):
    return "---" if value is None else f"{value:,.2f}"


def percent_string(value):
    if value is None:
        return "---"
    return f"{value:+.3f}%"


def age_string(updated):
    if updated is None:
        return "WAITING"
    seconds = (utc_now() - updated).total_seconds()
    return f"{seconds * 1000:.0f} ms" if seconds < 1 else f"{seconds:.1f} s"


def histories_snapshot():
    return {key: list(history) for key, history in candles.history.items()}


def process_price(connection, symbol, price, bid, ask, timestamp=None):
    if symbol not in market_state or price is None:
        return

    timestamp = timestamp or utc_now()
    state = market_state[symbol]
    state.update(price=price, bid=bid, ask=ask, updated=timestamp)
    state["quotes"] += 1
    momentum.add_price(symbol, price)

    for position in paper.on_price(symbol, price, timestamp):
        upsert_paper_trade(connection, position, timestamp.isoformat())

    closed_candles = candles.update(symbol, price, timestamp)
    for candle in closed_candles:
        save_candle(connection, candle)
        setup = strategy.on_candle(
            candle.symbol,
            candle.timeframe,
            histories_snapshot(),
        )
        if setup:
            save_setup(connection, setup)
            position = paper.register_setup(setup)
            upsert_paper_trade(connection, position, timestamp.isoformat())


def build_dashboard():
    table = Table(
        title="OTR MARKET • FUTURES STRATEGY ENGINE",
        caption="OPERATION 3 • NQ / ES / GC + BTC • PAPER EXECUTION ONLY 🔒",
    )
    table.add_column("Market")
    table.add_column("Price", justify="right")
    table.add_column("1s", justify="right")
    table.add_column("5s", justify="right")
    table.add_column("15s", justify="right")
    table.add_column("1m", justify="right")
    table.add_column("Age", justify="right")
    table.add_column("Quotes", justify="right")

    for symbol, state in market_state.items():
        returns = momentum.returns(symbol)
        table.add_row(
            state["name"],
            price_string(state["price"]),
            percent_string(returns["1s"]),
            percent_string(returns["5s"]),
            percent_string(returns["15s"]),
            percent_string(returns["1m"]),
            age_string(state["updated"]),
            f'{state["quotes"]:,}',
        )

    table.add_section()
    table.add_row("Coinbase", feed_status["Coinbase"], "", "", "", "", "", "")
    table.add_row("NinjaTrader", feed_status["NinjaTrader"], "", "", "", "", "", "")
    table.add_row(
        "Paper",
        f"Pending {paper.pending_count} / Open {paper.open_count}",
        "",
        "",
        "",
        "",
        "",
        f"R {paper.total_r:+.2f}",
    )

    if strategy.last_setup:
        setup = strategy.last_setup
        table.add_section()
        table.add_row(
            "LAST SETUP",
            f"{setup.symbol} {setup.timeframe}",
            setup.direction.upper(),
            f"E {setup.entry_price:.2f}",
            f"S {setup.stop_price:.2f}",
            f"T {setup.target_price:.2f}",
            f"RR {setup.risk_reward:.2f}",
            setup.trigger_type,
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
                            now = utc_now()
                            process_price(connection, "BTC-USD", price, bid, ask, now)
                            save_quote(
                                connection,
                                now.isoformat(),
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


def _latest_ninjatrader_id(connection) -> int:
    try:
        row = connection.execute(
            "SELECT COALESCE(MAX(id), 0) FROM market_quotes WHERE source LIKE 'ninjatrader:%'"
        ).fetchone()
        return int(row[0] or 0)
    except sqlite3.Error:
        return 0


async def ninjatrader_collector(connection):
    """
    Consume new NinjaTrader bridge ticks already written into SQLite by the
    FastAPI bridge endpoint. Keeping network ingress in the web process and
    strategy evaluation in this process makes the bridge easy to audit.
    """
    last_id = _latest_ninjatrader_id(connection)
    feed_status["NinjaTrader"] = "WAITING"

    while True:
        try:
            rows = connection.execute(
                """
                SELECT id, received_at, symbol, price, bid, ask, source
                FROM market_quotes
                WHERE id > ? AND source LIKE 'ninjatrader:%'
                ORDER BY id ASC
                LIMIT 1000
                """,
                (last_id,),
            ).fetchall()

            if rows:
                feed_status["NinjaTrader"] = "CONNECTED"
                for row in rows:
                    last_id = int(row[0])
                    symbol = row[2]
                    if symbol not in ("NQ", "ES", "GC"):
                        continue
                    price = row[3]
                    bid = row[4]
                    ask = row[5]
                    timestamp = parse_timestamp(row[1])
                    process_price(connection, symbol, price, bid, ask, timestamp)
            else:
                newest = connection.execute(
                    """
                    SELECT received_at
                    FROM market_quotes
                    WHERE source LIKE 'ninjatrader:%'
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ).fetchone()
                if newest:
                    age = (utc_now() - parse_timestamp(newest[0])).total_seconds()
                    feed_status["NinjaTrader"] = "CONNECTED" if age < 5 else "STALE"
                else:
                    feed_status["NinjaTrader"] = "WAITING"

            await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            feed_status["NinjaTrader"] = "ERROR"
            console.log(f"NinjaTrader bridge error: {exc}")
            await asyncio.sleep(1)


async def dashboard():
    with Live(build_dashboard(), console=console, refresh_per_second=4) as live:
        while True:
            live.update(build_dashboard())
            await asyncio.sleep(0.25)


async def main():
    connection = get_connection()
    try:
        await asyncio.gather(
            coinbase_collector(connection),
            ninjatrader_collector(connection),
            dashboard(),
        )
    finally:
        connection.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n[yellow]OTR Market stopped.[/yellow]")
