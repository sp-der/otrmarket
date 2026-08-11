import asyncio
import json
import os
from datetime import datetime, timezone

import websockets
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table


from pathlib import Path

ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(dotenv_path=ENV_PATH)

ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_API_SECRET = os.getenv("ALPACA_API_SECRET")

ALPACA_WS_URL = "wss://stream.data.alpaca.markets/v2/iex"

SYMBOLS = ["QQQ", "SPY"]

console = Console()


def utc_now_string() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def build_table(latest_quotes: dict) -> Table:
    table = Table(title="OTR Market - Nasdaq + S&P Live")

    table.add_column("Symbol")
    table.add_column("Bid", justify="right")
    table.add_column("Ask", justify="right")
    table.add_column("Mid", justify="right")
    table.add_column("Spread", justify="right")
    table.add_column("Time", justify="right")

    for symbol in SYMBOLS:
        quote = latest_quotes.get(symbol)

        if not quote:
            table.add_row(
                symbol,
                "...",
                "...",
                "...",
                "...",
                "...",
            )
            continue

        bid = quote["bid"]
        ask = quote["ask"]

        mid = (bid + ask) / 2
        spread = ask - bid

        table.add_row(
            symbol,
            f"${bid:,.2f}",
            f"${ask:,.2f}",
            f"${mid:,.2f}",
            f"${spread:,.4f}",
            quote["time"],
        )

    return table


async def stream_indices():
    if not ALPACA_API_KEY or not ALPACA_API_SECRET:
        raise RuntimeError(
            "Missing ALPACA_API_KEY or ALPACA_API_SECRET in .env"
        )

    latest_quotes = {}

    while True:
        try:
            console.print("[cyan]Connecting to Alpaca IEX stream...[/cyan]")

            async with websockets.connect(
                ALPACA_WS_URL,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=10,
            ) as websocket:

                # Authenticate
                auth_message = {
                    "action": "auth",
                    "key": ALPACA_API_KEY,
                    "secret": ALPACA_API_SECRET,
                }

                await websocket.send(json.dumps(auth_message))

                auth_response = json.loads(await websocket.recv())

                console.print(f"[dim]Auth response: {auth_response}[/dim]")

                # Subscribe to quote updates
                subscribe_message = {
                    "action": "subscribe",
                    "quotes": SYMBOLS,
                }

                await websocket.send(json.dumps(subscribe_message))

                console.print(
                    "[green]Connected. Watching QQQ and SPY.[/green]"
                )

                async for message in websocket:
                    data = json.loads(message)

                    if not isinstance(data, list):
                        continue

                    for event in data:
                        if event.get("T") != "q":
                            continue

                        symbol = event.get("S")

                        if symbol not in SYMBOLS:
                            continue

                        bid = float(event["bp"])
                        ask = float(event["ap"])

                        latest_quotes[symbol] = {
                            "bid": bid,
                            "ask": ask,
                            "time": utc_now_string(),
                        }

                    if latest_quotes:
                        console.clear()
                        console.print(build_table(latest_quotes))

        except asyncio.CancelledError:
            raise

        except KeyboardInterrupt:
            break

        except Exception as exc:
            console.print(
                f"[red]Connection error:[/red] {exc}"
            )
            console.print(
                "[yellow]Retrying in 5 seconds...[/yellow]"
            )
            await asyncio.sleep(5)


if __name__ == "__main__":
    try:
        asyncio.run(stream_indices())

    except KeyboardInterrupt:
        console.print(
            "\n[yellow]OTR Market stopped.[/yellow]"
        )