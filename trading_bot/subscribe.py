import websockets
import asyncio
import json
from datetime import datetime

from .market_analyzer import MarketAnalyzer
from .config import TRADING_CONFIG
import os
from dotenv import load_dotenv


load_dotenv()
API_KEY = os.getenv('API_KEY')
SYMBOL = "ETHUSDT"
PING_INTERVAL = 20  # секунд
RECONNECT_DELAY = 5  # секунд при ошибке


async def send_heartbeat(ws):
    while True:
        await asyncio.sleep(PING_INTERVAL)
        try:
            await ws.send(json.dumps({"op": "ping"}))
            print(f"Heartbeat sent: {datetime.utcnow().isoformat()}")
        except:
            # Если соединение разорвано, выйдем из цикла
            break


async def subscribe(ws):
    subscribe_msg = {
        "op": "subscribe",
        "args": [
            f"kline.5.{SYMBOL}",
            # f"kline.15.{SYMBOL}"
            # f"kline.30.{SYMBOL}"
        ]
    }
    await ws.send(json.dumps(subscribe_msg))

analyzer = MarketAnalyzer(config=TRADING_CONFIG)


async def handle_data(ws):
    while True:
        try:
            raw_data = await ws.recv()
            print(f"Получены данные: {raw_data}")
            data = json.loads(raw_data)

            if data.get("data") and data["data"][0].get("confirm"):
                candle = data["data"][0]
                if candle.get("interval") == "15":
                    analyzer.update_15m_candle(candle)
                    print(f"15‑минутная свеча обновлена для зон: {candle}")
                else:
                    analyzer.zigzag.update(candle)
                    analyzer.volume_ma.append(candle['volume'])
                    analyzer.atr_ma.append(candle.get('atr', 0.0))

                    signal = analyzer.generate_signal(candle)
                    if signal:
                        print(f"Сигнал сформирован: {signal}")
        except websockets.exceptions.ConnectionClosed:
            print("Connection closed, reconnecting...")
            await asyncio.sleep(RECONNECT_DELAY)
            break
        except Exception as e:
            print(f"Error: {str(e)}")
            break


async def main():
    while True:
        try:
            async with websockets.connect("wss://stream.bybit.com/v5/public/linear") as ws:
                print("Connected!")
                await subscribe(ws)
                heartbeat_task = asyncio.create_task(send_heartbeat(ws))
                await handle_data(ws)
                heartbeat_task.cancel()
        except Exception as e:
            await ws.close()
            print(f"Connection error: {str(e)}, retrying...")
            await asyncio.sleep(RECONNECT_DELAY)
