from .config import TRADING_CONFIG
from .market_analyzer import MarketAnalyzer
import json
import websockets
import asyncio

RECONNECT_DELAY = 5  # секунд

analyzer = MarketAnalyzer(config=TRADING_CONFIG)


async def handle_data(ws):
    while True:
        try:
            raw_data = await ws.recv()
            print(f"Получены данные: {raw_data}")
            data = json.loads(raw_data)

            if data.get("data") and data["data"][0].get("confirm"):
                print(f"Closed Candle: {data}")
                candle = data["data"][0]

                analyzer.zigzag.update(candle)
        except websockets.exceptions.ConnectionClosed:
            print("Connection closed, reconnecting...")
            await asyncio.sleep(RECONNECT_DELAY)
            break
        except Exception as e:
            print(f"Error: {str(e)}")
            break
