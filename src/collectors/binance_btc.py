import asyncio
import json
from datetime import datetime, timezone

import websockets
from rich.console import Console
from rich.table import Table


BINANCE_WS_URL = "wss://stream.binance.com:9443/ws/btcusdt@bookTicker"

console = Console()


def utc_now_string() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def build_table(bid: float, ask: float, bid_qty: float, ask_qty: float) -> Table:
    spread = ask - bid
    mid = (bid + ask) / 2
    spread_bps = (spread / mid) * 10_000 if mid else 0

    table = Table(title="OTR Market - BTCUSDT Live")

    table.add_column("Metric")
    table.add_column("Value", justify="right")

    table.add_row("UTC Time", utc_now_string())
    table.add_row("Bid", f"${bid:,.2f}")
    table.add_row("Ask", f"${ask:,.2f}")
    table.add_row("Mid", f"${mid:,.2f}")
    table.add_row("Spread", f"${spread:,.4f}")
    table.add_row("Spread", f"{spread_bps:.4f} bps")
    table.add_row("Bid Qty", f"{bid_qty:,.6f} BTC")
    table.add_row("Ask Qty", f"{ask_qty:,.6f} BTC")

    return table


async def stream_btc():
    while True:
        try:
            console.print("[cyan]Connecting to Binance BTCUSDT stream...[/cyan]")

            async with websockets.connect(
                BINANCE_WS_URL,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=10,
            ) as websocket:

                console.print("[green]Connected.[/green]")

                async for message in websocket:
                    data = json.loads(message)

                    bid = float(data["b"])
                    bid_qty = float(data["B"])
                    ask = float(data["a"])
                    ask_qty = float(data["A"])

                    console.clear()
                    console.print(
                        build_table(
                            bid=bid,
                            ask=ask,
                            bid_qty=bid_qty,
                            ask_qty=ask_qty,
                        )
                    )

        except asyncio.CancelledError:
            raise

        except KeyboardInterrupt:
            console.print("\n[yellow]Stopped by user.[/yellow]")
            break

        except Exception as exc:
            console.print(
                f"[red]Connection error:[/red] {exc}"
            )
            console.print("[yellow]Retrying in 5 seconds...[/yellow]")
            await asyncio.sleep(5)


if __name__ == "__main__":
    try:
        asyncio.run(stream_btc())
    except KeyboardInterrupt:
        console.print("\n[yellow]OTR Market stopped.[/yellow]")