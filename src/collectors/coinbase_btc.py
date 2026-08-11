import asyncio
import json
from datetime import datetime, timezone

import websockets
from rich.console import Console
from rich.table import Table

from src.storage.database import get_connection, save_btc_quote


COINBASE_WS_URL = "wss://advanced-trade-ws.coinbase.com"

console = Console()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_table(
    price: float,
    best_bid: float,
    best_ask: float,
    quotes_saved: int,
) -> Table:

    spread = best_ask - best_bid
    mid = (best_bid + best_ask) / 2
    spread_bps = (spread / mid) * 10_000 if mid else 0

    table = Table(title="OTR Market - BTC-USD Live")

    table.add_column("Metric")
    table.add_column("Value", justify="right")

    table.add_row(
        "UTC Time",
        datetime.now(timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S.%f"
        )[:-3],
    )

    table.add_row("Last Price", f"${price:,.2f}")
    table.add_row("Bid", f"${best_bid:,.2f}")
    table.add_row("Ask", f"${best_ask:,.2f}")
    table.add_row("Mid", f"${mid:,.2f}")
    table.add_row("Spread", f"${spread:,.4f}")
    table.add_row("Spread", f"{spread_bps:.4f} bps")
    table.add_row("Quotes Saved", f"{quotes_saved:,}")

    return table


async def stream_btc():

    connection = get_connection()

    quotes_saved = 0

    while True:

        try:

            console.print(
                "[cyan]Connecting to Coinbase BTC-USD stream...[/cyan]"
            )

            async with websockets.connect(
                COINBASE_WS_URL,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=10,
            ) as websocket:

                subscribe_message = {
                    "type": "subscribe",
                    "product_ids": ["BTC-USD"],
                    "channel": "ticker",
                }

                await websocket.send(
                    json.dumps(subscribe_message)
                )

                console.print("[green]Connected.[/green]")

                async for message in websocket:

                    data = json.loads(message)

                    if data.get("channel") != "ticker":
                        continue

                    exchange_time = data.get("timestamp")

                    events = data.get("events", [])

                    for event in events:

                        tickers = event.get("tickers", [])

                        for ticker in tickers:

                            if ticker.get("product_id") != "BTC-USD":
                                continue

                            price = float(ticker["price"])
                            best_bid = float(ticker["best_bid"])
                            best_ask = float(ticker["best_ask"])

                            save_btc_quote(
                                connection=connection,
                                received_at=utc_now_iso(),
                                exchange_time=exchange_time,
                                exchange="coinbase",
                                symbol="BTC-USD",
                                price=price,
                                bid=best_bid,
                                ask=best_ask,
                            )

                            quotes_saved += 1

                            console.clear()

                            console.print(
                                build_table(
                                    price=price,
                                    best_bid=best_bid,
                                    best_ask=best_ask,
                                    quotes_saved=quotes_saved,
                                )
                            )

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
        asyncio.run(stream_btc())

    except KeyboardInterrupt:
        console.print(
            "\n[yellow]OTR Market stopped.[/yellow]"
        )