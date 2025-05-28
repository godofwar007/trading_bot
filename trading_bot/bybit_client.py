# bybit_client.py
import time
import logging
import requests
from pybit.unified_trading import HTTP, WebSocket
from .config import BYBIT_API_KEY, BYBIT_API_SECRET, SYMBOL


class BybitClient:
    def __init__(self):
        self.http_client = HTTP(
            api_key=BYBIT_API_KEY,
            api_secret=BYBIT_API_SECRET,
            testnet=False
        )
        self.ws = WebSocket(
            api_key=BYBIT_API_KEY,
            api_secret=BYBIT_API_SECRET,
            channel_type="private",
            testnet=False
        )

        self.subscribe_to_order_updates()
        self.processed_messages = set()

    def get_historical_kline(self, symbol: str, limit: int = 150, interval: str = "5"):

        import logging
        try:
            # Для Bybit Unified v5: /v5/market/kline
            # ВАЖНО: category="linear" для фьючерсных контрактов USDT.

            response = self.http_client.get_kline(
                category="linear",
                symbol=symbol,
                interval=interval,
                limit=limit
            )
            # Лог для отладки
            logging.info(f"get_historical_kline response: {response}")

            if response.get("retCode") == 0:
                raw_candles = response["result"]["list"]
                if not raw_candles:
                    logging.warning(
                        "get_historical_kline: Пустой список свечей")
                    return []

                candles = []
                for c in raw_candles:
                    candles.append({
                        "timestamp": int(c[0]),
                        "open": float(c[1]),
                        "high": float(c[2]),
                        "low": float(c[3]),
                        "close": float(c[4]),
                        "volume": float(c[5]),
                        "confirm": True
                    })
                return candles
            else:
                logging.error(
                    f"get_historical_kline error: {response.get('retMsg')}")
                return []
        except Exception as e:
            logging.error(f"get_historical_kline exception: {e}")
            return []

    def subscribe_to_order_updates(self):
        # Подписка на обычные ордера
        self.ws.subscribe(topic="order", symbol=SYMBOL,
                          callback=self.handle_ws_message)

    def handle_ws_message(self, message):
        logging.info(f"WebSocket message: {message}")
        if message.get("topic") == "order":
            for order in message.get("data", []):
                message_key = f"{order.get('orderId')}_{order.get('updatedTime')}"
                if message_key in self.processed_messages:
                    logging.debug(
                        f"Skipping duplicate WebSocket message: {message_key}")
                    continue
                self.processed_messages.add(message_key)
                self.handle_order_update(order)
                # Очистка старых сообщений для экономии памяти
                if len(self.processed_messages) > 1000:
                    self.processed_messages.clear()

    def handle_order_update(self, order_data):
        order_id = order_data.get("orderId")
        status = order_data.get("orderStatus")
        if hasattr(self, "order_callback"):
            self.order_callback(order_id, status, order_data)

    def track_order_status(self, callback):
        """Регистрация колбэка для отслеживания статусов ордеров."""
        self.order_callback = callback

    def _get_server_timestamp(self) -> int:
        """Получает серверное время Bybit в миллисекундах."""
        try:
            response = requests.get(
                "https://api.bybit.com/v5/market/time", timeout=5)
            return int(response.json()["time"])
        except Exception as e:
            logging.error(f"Ошибка получения времени Bybit: {e}")
            return int(time.time() * 1000)  # fallback на локальное время

    def place_active_order(self, symbol, side: str, qty: float):

        try:
            ts = self._get_server_timestamp()
            order = self.http_client.place_order(
                category="linear",
                symbol=symbol,
                side=side,
                orderType="Market",
                qty=str(qty),  # Приводим количество к строке
                timeInForce="GTC",
                reduceOnly=False,
                closeOnTrigger=False,
                timestamp=ts,
                recv_window=15000
            )
            if order.get("retCode") == 0:
                order_id = order["result"]["orderId"]
                logging.info(f"Placed {side} market order: {order_id}")
                return order
            else:
                logging.error(f"Order failed: {order.get('retMsg')}")
                return None
        except Exception as e:
            logging.error(f"Error placing order: {e}")
            return None

    def get_unified_wallet_balance(self, retries=3) -> dict:
        for _ in range(retries):
            try:
                ts = self._get_server_timestamp()
                result = self.http_client.get_wallet_balance(
                    accountType="UNIFIED",
                    timestamp=ts,
                    recv_window=30000
                )
                if result.get("retCode") == 0:
                    return result
                elif result.get("retCode") == 10002:
                    logging.warning(
                        f"Retrying... Server time: {result.get('time')}, Request time: {ts}")
                    time.sleep(2.5)
                    continue
            except Exception as e:
                logging.error(f"Ошибка: {e}")
        return {}

    def place_conditional_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        stop_px: float,
        orderType: str = "Market",
        reduce_only: bool = True,
        triggerDirection: int = None,
        retries: int = 3
    ):
        if triggerDirection not in [1, 2]:
            raise ValueError(
                "triggerDirection должен быть 1 (цена выше) или 2 (цена ниже)")

        import time
        for attempt in range(retries):
            try:
                result = self.http_client.place_order(
                    category="linear",
                    symbol=symbol,
                    side=side,
                    orderType=orderType,
                    qty=str(qty),
                    triggerPrice=str(stop_px),
                    timeInForce="GTC",
                    triggerBy="LastPrice",
                    reduceOnly=reduce_only,
                    triggerDirection=triggerDirection
                )
                if result.get("retCode") == 0:
                    return result
                elif attempt < retries - 1:
                    time.sleep(1.5 ** attempt)
            except Exception as e:
                logging.error(f"Попытка {attempt+1} не удалась: {e}")
                if attempt == retries - 1:
                    raise Exception(
                        f"Не удалось разместить ордер после {retries} попыток: {e}")
        return None

    def set_sl_tp(self, position, symbol):
        try:
            side_sl = "Sell" if position["direction"] == "long" else "Buy"
            side_tp = side_sl

            # Stop Loss
            self.place_conditional_order(
                symbol, side_sl, position["qty"], position["sl"])

            # Take Profit (частичное закрытие)
            self.place_conditional_order(
                symbol, side_tp, position["qty"] / 2, position["tp1"])

            logging.info(f"SL/TP set for position: {position['order_id']}")
        except Exception as e:
            logging.error(f"Error setting SL/TP: {e}")

    def close_position(self, position, qty=None):
        qty = qty or position["qty"]
        side = "Sell" if position["direction"] == "long" else "Buy"
        return self.place_active_order(position['symbol'], side, qty)

    def get_current_price(self, symbol):
        """Новый метод для получения текущей цены"""
        try:
            ticker = self.http_client.get_tickers(
                category="linear",
                symbol=symbol
            )
            return float(ticker["result"]["list"][0]["lastPrice"])
        except Exception as e:
            logging.error(f"Price error: {e}")
            return None

    def get_symbol_info(self, symbol: str) -> dict:
        """Получить информацию о символе (мин. объём и т.д.)"""
        try:
            response = self.http_client.get_instruments_info(
                category="linear",
                symbol=symbol
            )
            if response["retCode"] == 0:
                return response["result"]["list"][0]
            return {}
        except Exception as e:
            logging.error(f"Ошибка получения информации: {e}")
            return {}

    def get_closed_trades(self, symbol: str, limit: int = 50):
        """
        Возвращает список последних закрытых сделок по символу.
        sign_request сам добавляет timestamp и подпись.
        """
        path = "/private/linear/trade/execution/list"
        params = {
            "symbol": symbol,
            "limit": limit,

        }
        resp = self.sign_request("GET", path, params)
        return resp.get("result", {}).get("dataList", [])
