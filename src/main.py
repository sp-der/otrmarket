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
from src.runtime.clock import MarketClock
from src.storage.database import (
    get_connection,
    get_engine_state,
    load_recent_candles,
    save_candle,
    save_diagnostic,
    save_quote,
    save_setup,
    set_engine_state,
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
clock = MarketClock()

market_state = {
    "BTC-USD": {"name": "Bitcoin", "source": "Coinbase", "price": None, "bid": None, "ask": None, "quotes": 0},
    "NQ": {"name": "Nasdaq Futures", "source": "NinjaTrader", "price": None, "bid": None, "ask": None, "quotes": 0},
    "ES": {"name": "S&P 500 Futures", "source": "NinjaTrader", "price": None, "bid": None, "ask": None, "quotes": 0},
    "GC": {"name": "Gold Futures", "source": "NinjaTrader", "price": None, "bid": None, "ask": None, "quotes": 0},
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


def stream_state(symbol: str) -> str:
    mode = clock.mode(symbol)
    age = clock.ingress_age_seconds(symbol)
    if age is None:
        return "WAITING"
    if age > 10:
        return "STALE"
    return mode


def histories_snapshot():
    return {key: list(history) for key, history in candles.history.items()}


def evaluate_strategy(connection, symbol: str, timeframe: str):
    setup = strategy.on_candle(symbol, timeframe, histories_snapshot())
    save_diagnostic(connection, strategy.diagnostic(symbol, timeframe))
    if setup:
        save_setup(connection, setup)
        position = paper.register_setup(setup)
        upsert_paper_trade(connection, position, setup.created_at.isoformat())
    return setup


def process_price(connection, symbol, price, bid, ask, timestamp=None):
    if symbol not in market_state or price is None:
        return

    timestamp = timestamp or utc_now()
    ingest_time = utc_now()
    state = market_state[symbol]
    state.update(price=price, bid=bid, ask=ask)
    state["quotes"] += 1
    clock.update(symbol, timestamp, ingest_time)
    momentum.add_price(symbol, price, timestamp)

    for position in paper.on_price(symbol, price, timestamp):
        upsert_paper_trade(connection, position, timestamp.isoformat())

    closed_candles = candles.update(symbol, price, timestamp)
    for candle in closed_candles:
        save_candle(connection, candle)

        # Evaluate the candle that just closed.
        evaluate_strategy(connection, candle.symbol, candle.timeframe)

        # NQ/ES SMT requires both markets to have the same close-time available.
        # Re-evaluate the paired market after either side closes. Stage gating in
        # ConfluenceEngine prevents duplicate same-bar transitions while allowing
        # SMT to appear once the second market catches up.
        if candle.symbol in ("NQ", "ES"):
            pair = "ES" if candle.symbol == "NQ" else "NQ"
            pair_history = candles.get_history(pair, candle.timeframe)
            if pair_history and pair_history[-1].close_time == candle.close_time:
                evaluate_strategy(connection, pair, candle.timeframe)


def build_dashboard():
    table = Table(
        title="OTR MARKET • STRATEGY LAB",
        caption="OPERATION 4 • REPLAY-AWARE CONFLUENCE + PAPER EXECUTION • LIVE ORDERS DISABLED 🔒",
    )
    table.add_column("Market")
    table.add_column("Price", justify="right")
    table.add_column("1s", justify="right")
    table.add_column("5s", justify="right")
    table.add_column("15s", justify="right")
    table.add_column("1m", justify="right")
    table.add_column("Mode", justify="right")
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
            stream_state(symbol),
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

    # Show the most advanced current scanner state.
    ranked = sorted(
        strategy.diagnostics.values(),
        key=lambda d: (
            sum(bool(d.get(k)) for k in ("pd_array", "signal", "displacement", "entry_fvg", "retracement", "rr")),
            d.get("market_time", ""),
        ),
        reverse=True,
    )
    if ranked:
        diag = ranked[0]
        table.add_section()
        table.add_row(
            "SCANNER",
            f'{diag["symbol"]} {diag["timeframe"]}',
            diag.get("stage", ""),
            "PD✓" if diag.get("pd_array") else "PD·",
            "SIG✓" if diag.get("signal") else "SIG·",
            "DISP✓" if diag.get("displacement") else "DISP·",
            "FVG✓" if diag.get("entry_fvg") else "FVG·",
            diag.get("trigger_type") or "",
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
                            event_time = parse_timestamp(exchange_time)
                            process_price(connection, "BTC-USD", price, bid, ask, event_time)
                            save_quote(
                                connection,
                                event_time.isoformat(),
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
    saved = get_engine_state(connection, "last_ninjatrader_quote_id")
    if saved is not None:
        try:
            return int(saved)
        except ValueError:
            pass
    try:
        row = connection.execute(
            "SELECT COALESCE(MAX(id), 0) FROM market_quotes WHERE source LIKE 'ninjatrader:%'"
        ).fetchone()
        value = int(row[0] or 0)
        set_engine_state(connection, "last_ninjatrader_quote_id", str(value))
        return value
    except sqlite3.Error:
        return 0


async def ninjatrader_collector(connection):
    """Consume bridge ticks already written by the FastAPI ingress process."""
    last_id = _latest_ninjatrader_id(connection)
    feed_status["NinjaTrader"] = "WAITING"

    while True:
        try:
            rows = connection.execute(
                """
                SELECT id, received_at, symbol, price, bid, ask, source, ingested_at
                FROM market_quotes
                WHERE id > ? AND source LIKE 'ninjatrader:%'
                ORDER BY id ASC
                LIMIT 2000
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
                set_engine_state(connection, "last_ninjatrader_quote_id", str(last_id))
            else:
                newest = connection.execute(
                    """
                    SELECT ingested_at
                    FROM market_quotes
                    WHERE source LIKE 'ninjatrader:%'
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ).fetchone()
                if newest and newest[0]:
                    age = (utc_now() - parse_timestamp(newest[0])).total_seconds()
                    feed_status["NinjaTrader"] = "CONNECTED" if age < 10 else "STALE"
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

    # Restore enough completed candles for swings, FVGs, displacement and SMT
    # to survive engine restarts. No historical trades are re-executed here.
    seeded = load_recent_candles(
        connection,
        symbols=("NQ", "ES", "GC", "BTC-USD"),
        timeframes=("1m", "5m", "15m", "1h"),
        limit_per_series=500,
    )
    candles.seed_history(seeded)
    if seeded:
        console.log(f"Seeded {len(seeded)} completed candles from SQLite")

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
