import logging
import time
from .bybit_client import BybitClient
from .utils import send_telegram_message
import math


class PositionManager:
    def __init__(self):
        self.client = BybitClient()
        self.active_positions = []
        self.closed_positions = []

        self.client.track_order_status(self.handle_order_status)
        self.tp_mode = "dual"      # по умолчанию SL+TP1+TP2

    def set_tp_mode(self, mode: str) -> None:

        if mode not in ("single", "dual"):
            raise ValueError("tp_mode must be 'single' or 'dual'")
        self.tp_mode = mode

    def set_sl_tp(self, position: dict, symbol: str) -> None:

        try:
            side = "Sell" if position["direction"] == "long" else "Buy"

            info = self.client.get_symbol_info(symbol)
            price_step = float(info["priceFilter"]["tickSize"])
            qty_step = float(info["lotSizeFilter"]["qtyStep"])
            min_qty = float(info["lotSizeFilter"]["minOrderQty"])

            # округляем цены
            rounded_sl = math.floor(position["sl"] / price_step) * price_step
            rounded_tp1 = math.floor(position["tp1"] / price_step) * price_step
            rounded_tp2 = math.floor(position["tp2"] / price_step) * price_step

            # STOP‑LOSS
            trigger_dir = 2 if position["direction"] == "long" else 1
            sl_order = self.client.place_conditional_order(
                symbol=symbol, side=side, qty=position["qty"],
                stop_px=rounded_sl, orderType="Market",
                reduce_only=True, triggerDirection=trigger_dir
            )
            if sl_order.get("retCode") != 0:
                logging.error(f"SL не установлен: {sl_order.get('retMsg')}")
                return
            position["sl_order_id"] = sl_order["result"]["orderId"]
            position["active_orders"].append(position["sl_order_id"])

            # делим объём, если нужен второй ТР
            units_total = int(round(position["qty"] / qty_step))
            if self.tp_mode == "dual":
                tp1_units = (2 * units_total) // 3
                tp2_units = units_total - tp1_units
            else:
                tp1_units, tp2_units = units_total, 0

            create_tp2 = tp2_units * qty_step >= min_qty
            if self.tp_mode == "single" or not create_tp2:
                tp1_units, tp2_units = units_total, 0   # весь объём — TP1

            tp1_qty = tp1_units * qty_step
            tp2_qty = tp2_units * qty_step
            dec = len(str(qty_step).split(".")[1])
            tp1_qty_s = f"{tp1_qty:.{dec}f}"
            tp2_qty_s = f"{tp2_qty:.{dec}f}"

            # единственный TP
            tp1_order = self.client.http_client.place_order(
                category="linear", symbol=symbol, side=side, orderType="Limit",
                qty=tp1_qty_s, price=str(rounded_tp1),
                timeInForce="GTC", reduceOnly=True
            )
            if tp1_order.get("retCode") != 0:
                logging.error(f"TP1 не создан: {tp1_order.get('retMsg')}")
                return
            position["tp1_order_id"] = tp1_order["result"]["orderId"]
            position["active_orders"].append(position["tp1_order_id"])

            # TP2
            if self.tp_mode == "dual" and create_tp2:
                tp2_order = self.client.http_client.place_order(
                    category="linear", symbol=symbol, side=side,
                    orderType="Limit", qty=tp2_qty_s, price=str(rounded_tp2),
                    timeInForce="GTC", reduceOnly=True
                )
                if tp2_order.get("retCode") != 0:
                    logging.error(f"TP2 не создан: {tp2_order.get('retMsg')}")
                else:
                    position["tp2_order_id"] = tp2_order["result"]["orderId"]
                    position["active_orders"].append(position["tp2_order_id"])

            logging.info(
                f"SL/TP‑ордер(а) установлены для {position['order_id']}")

        except Exception as e:
            logging.error(f"Ошибка в set_sl_tp: {e}")

    def handle_tp1_filled(self, position):
        """
        Сработал TP1: половина позиции закрыта.
        отменяем старый SL
        вычисляем новый объём (1/3 позиции) и новый SL
        """
        try:
            # актуальный объём позиции
            pos_resp = self.client.http_client.get_positions(
                category="linear", symbol=position["symbol"]
            )
            if pos_resp.get("retCode") != 0 or not pos_resp["result"]["list"]:
                logging.error(
                    f"Не удалось получить позицию: {pos_resp.get('retMsg')}")
                return
            current_qty = float(pos_resp["result"]["list"][0]["size"])

            # отменяем старый SL
            if position.get("sl_order_id"):
                self.client.http_client.cancel_order(
                    category="linear", symbol=position["symbol"],
                    orderId=position["sl_order_id"]
                )
                logging.info(f"Старый SL отменён: {position['sl_order_id']}")
                if position["sl_order_id"] in position["active_orders"]:
                    position["active_orders"].remove(position["sl_order_id"])
                else:
                    logging.warning(
                        f"SL ордер {position['sl_order_id']} не найден в active_orders")
            symbol_info = self.client.get_symbol_info(position["symbol"])
            tick_size = float(symbol_info["priceFilter"]["tickSize"])
            qty_step = float(symbol_info["lotSizeFilter"]["qtyStep"])
            min_qty = float(symbol_info["lotSizeFilter"]["minOrderQty"])

            # рассчитываем новый SL
            new_sl_raw = self.calculate_new_sl(position)
            new_sl = math.floor(new_sl_raw / tick_size) * tick_size

            def round_step(val, step):            # округление вниз
                return math.floor(val / step) * step

            remaining_qty = round_step(current_qty, qty_step)
            if remaining_qty < min_qty:
                remaining_qty = min_qty           # ставим min‑lot

            dec = len(str(qty_step).split(".")[1])
            qty_str = f"{remaining_qty:.{dec}f}"  # строка нужной точности

            # размещаем новый SL
            side = "Sell" if position["direction"] == "long" else "Buy"
            trigger_dir = 2 if position["direction"] == "long" else 1
            new_sl_order = self.client.place_conditional_order(
                symbol=position["symbol"], side=side, qty=qty_str,
                stop_px=new_sl, orderType="Market", reduce_only=True,
                triggerDirection=trigger_dir
            )

            if new_sl_order.get("retCode") == 0:
                position["sl_order_id"] = new_sl_order["result"]["orderId"]
                position["active_orders"].append(position["sl_order_id"])
                position["sl"] = new_sl
                position["qty"] = remaining_qty
                position["tp1_hit"] = True
                logging.info(f"New SL {new_sl} qty {qty_str} поставлен")
                send_telegram_message({
                    "position_partially_closed": True,
                    "position": {
                        "symbol": position["symbol"],
                        "direction": position["direction"],
                        "entry": position["entry"],
                        "sl": new_sl,
                        "qty": remaining_qty,
                        "order_id": position["order_id"]
                    }
                })
            else:
                err = new_sl_order.get("retMsg")
                logging.error(f"Не удалось поставить новый SL: {err}")
                send_telegram_message(f"⚠️ Ошибка установки нового SL: {err}")

        except Exception as e:
            logging.error(f"Ошибка обработки TP1: {e}")
            send_telegram_message(f"⚠️ Ошибка при обработке TP1: {e}")

    def calculate_new_sl(self, position):
        entry_price = position["entry"]
        direction = position["direction"]
        commission_rate = 0.0020  # 0.20% комиссии
        if direction == "long":
            new_sl = entry_price * (1 + commission_rate)
        else:
            new_sl = entry_price * (1 - commission_rate)
        return round(new_sl, 2)

    def close_position(self, position, qty=None, reason=""):
        qty = qty or position["qty"]
        current_price = self.client.get_current_price(position['symbol'])

        # Отмена всех активных ордеров
        for oid in position.get("active_orders", []):
            for attempt in range(3):  # 3 попытки
                try:
                    cancel_resp = self.client.http_client.cancel_order(
                        category="linear",
                        symbol=position["symbol"],
                        orderId=oid
                    )
                    if cancel_resp.get("retCode") == 0:
                        logging.info(f"Отменён ордер {oid}")
                    elif cancel_resp.get("retMsg") in ("Order already cancelled",
                                                       "Order not exists"):
                        logging.info(
                            f"Ордер {oid} уже отсутствует (retMsg={cancel_resp.get('retMsg')})")
                    else:
                        logging.warning(
                            f"Не удалось отменить ордер {oid}: {cancel_resp.get('retMsg')}")
                except Exception as e:
                    logging.error(f"Ошибка при отмене ордера {oid}: {e}")
                    time.sleep(1)

        # Расчет прибыли
        if position['direction'] == 'long':
            profit = (current_price - position['entry']) * position['qty']
        else:
            profit = (position['entry'] - current_price) * position['qty']

        closed_position = {
            **position,
            "close_price": current_price,
            "close_time": int(time.time() * 1000),
            "close_reason": reason,
            "profit": profit
        }
        self.closed_positions.append(closed_position)
        if position in self.active_positions:
            self.active_positions.remove(position)

        logging.info(
            f"Position {position['order_id']} closed by {reason}. Profit = {profit:.2f}")
        # уведомление о полном закрытии (SL или TP2)
        if not (reason == "TP" and self.tp_mode == "single"):
            send_telegram_message({
                "position_closed": True,
                "position": closed_position,
            })
        position["active_orders"].clear()
        return closed_position

    def market_close_active_position(self) -> dict | None:
        """
        Принудительно закрывает текущую позицию MARKET‑ордером и
        снимает все отложенные ордера.
        """
        if not self.active_positions:
            logging.info("Нет активной позиции для закрытия")
            return None

        position = self.active_positions[0]

        for oid in position.get("active_orders", []):
            try:
                self.client.http_client.cancel_order(
                    category="linear",
                    symbol=position["symbol"],
                    orderId=oid
                )
            except Exception as e:
                logging.warning(f"Не снят ордер {oid}: {e}")
        position["active_orders"].clear()

        side = "Sell" if position["direction"] == "long" else "Buy"
        resp = self.client.http_client.place_order(
            category="linear",
            symbol=position["symbol"],
            side=side,
            orderType="Market",
            qty=str(position["qty"]),
            reduceOnly=True
        )
        if resp.get("retCode") != 0:
            logging.error(
                f"Не удалось закрыть MARKET‑ордером: {resp.get('retMsg')}")
            return None
        order_id = resp["result"]["orderId"]

        # ждём, пока ордер исполнится
        if not self.wait_for_order_filled(order_id, position["symbol"]):
            logging.error("MARKET‑ордер не исполнился")
            return None

        # фиксируем закрытие
        closed = self.close_position(position, reason="ManualClose")
        return closed

    def open_position(self, signal, leverage, position_notional, symbol):
        if self.active_positions:
            logging.info("Позиция уже открыта, новая сделка не открывается.")
            return None

        try:
            balance_resp = self.client.get_unified_wallet_balance()
            if not balance_resp or balance_resp.get("retCode") != 0:
                logging.error("Не удалось получить баланс для проверки маржи")
                return None

            total_equity = float(
                balance_resp["result"]["list"][0]["totalEquity"])
            if total_equity < position_notional:
                logging.error(
                    f"Недостаточно средств. Баланс: {total_equity}, требуется: {position_notional}"
                )
                return None

            symbol_info = self.client.get_symbol_info(symbol)
            if not symbol_info:
                logging.error(f"Данные символа {symbol} не получены")
                send_telegram_message(
                    f"❌ Ошибка: не удалось получить данные для {symbol}")
                return None

            min_qty = float(symbol_info["lotSizeFilter"]["minOrderQty"])
            logging.info(f"Мин. объём для {symbol}: {min_qty}")

            last_price = self.client.get_current_price(symbol)
            if not last_price:
                raise Exception("Цена не получена")

            qty_step = float(symbol_info["lotSizeFilter"]["qtyStep"])
            raw_qty = (position_notional * leverage) / last_price
            qty = round(raw_qty / qty_step) * qty_step
            qty = max(qty, min_qty)
            logging.info(f"Расчётный объём: {qty} {symbol}")

            if qty < min_qty:
                error_msg = f"Объём {qty} < мин. {min_qty} {symbol}"
                logging.error(error_msg)
                send_telegram_message(f"❌ {error_msg}")
                return None

            positions = self.client.http_client.get_positions(
                category="linear",
                symbol=symbol
            )
            current_leverage = (
                float(positions["result"]["list"][0]["leverage"])
                if positions["result"]["list"]
                else None
            )
            if current_leverage != leverage:
                self.client.http_client.set_leverage(
                    category="linear",
                    symbol=symbol,
                    buyLeverage=str(leverage),
                    sellLeverage=str(leverage),
                )
                logging.info(f"Плечо обновлено: {leverage}x")

            side = "Buy" if signal["direction"] == "long" else "Sell"
            order_response = None
            for attempt in range(3):
                order_response = self.client.place_active_order(
                    symbol, side, qty)
                if order_response and order_response.get("retCode") == 0:
                    break
                time.sleep(2 ** attempt)
            else:
                raise Exception("Все попытки открытия ордера не удались")

            order_id = order_response["result"]["orderId"]
            logging.info(f"Размещён рыночный ордер {side}: {order_id}")

            if not self.wait_for_order_filled(order_id, symbol):
                logging.error(
                    f"Ордер {order_id} не был исполнен, пропускаем установку SL/TP")
                return None

            position = {
                "order_id": order_id,
                "direction": signal["direction"],
                "entry": last_price,
                "sl": signal["sl"],
                "tp1": signal["tp1"],
                "tp2": signal["tp2"],
                "qty": qty,
                "tp1_hit": False,
                "symbol": symbol,
                "notified_open": False,
                "closed": False,
                "active_orders": []
            }
            self.active_positions.append(position)
            time.sleep(1)
            self.set_sl_tp(position, symbol)

            if self.tp_mode == "single":
                send_telegram_message({
                    "position_single": {
                        "symbol": position["symbol"],
                        "direction": position["direction"].upper(),
                        "entry": position["entry"],
                        "qty": position["qty"],
                        "sl": position["sl"],
                        "tp": position["tp1"],
                    }
                })
            else:
                send_telegram_message({
                    "position": {
                        "symbol": position["symbol"],
                        "direction": position["direction"].upper(),
                        "entry": position["entry"],
                        "qty": position["qty"],
                        "sl": position["sl"],
                        "tp1": position["tp1"],
                        "tp2": position["tp2"],
                    }
                })
            position["notified_open"] = True

            return position

        except Exception as e:
            error_msg = f"Ошибка открытия позиции ({symbol}): {str(e)}"
            logging.error(error_msg)
            send_telegram_message(f"🔥 {error_msg}")
            return None

    def handle_order_status(self, order_id, status, order_data=None):
        """
        Вызывается из BybitClient, когда меняется статус ордера (WS-сообщение).
        Проверяем, как это влияет на нашу позицию.
        """
        position = None
        for p in self.active_positions:
            if order_id in [p.get("order_id"),
                            p.get("sl_order_id"),
                            p.get("tp1_order_id"),
                            p.get("tp2_order_id")]:
                position = p
                break

        if not position:
            return

        logging.info(f"Order status update: {order_id} -> {status}")

        if status in ("Rejected", "Expired", "Cancelled"):
            logging.warning(f"Order {order_id} {status}")
            return

        if status == "Filled":
            # уведомление об открытии
            if order_id == position.get("order_id") and not position.get("notified_open"):
                send_telegram_message({
                    "position": {
                        "symbol": position["symbol"],
                        "direction": position["direction"].upper(),
                        "entry": position["entry"],
                        "qty": position["qty"],
                        "sl": position["sl"],
                        "tp1": position["tp1"],
                        "tp2": position["tp2"]
                    }
                })
                position["notified_open"] = True

            elif order_id == position.get("tp1_order_id"):
                if self.tp_mode == "single":
                    # вычисляем текущую цену и профит
                    close_price = self.client.get_current_price(
                        position['symbol'])
                    profit = (
                        (close_price - position['entry']) * position['qty']
                        if position['direction'] == 'long'
                        else (position['entry'] - close_price) * position['qty']
                    )
                    # отправляем отдельное TP‐уведомление
                    send_telegram_message({
                        "position_tp": True,
                        "position": {
                            **position,
                            "close_price": close_price,
                            "profit": profit
                        }
                    })
                    # затем закрываем позицию как обычно
                    self.close_position(position, reason="TP")
                else:
                    # dual-режим — частичное закрытие и перестановка SL
                    self.handle_tp1_filled(position)

            elif order_id == position.get("tp2_order_id"):
                self.close_position(position, reason="TP2")

            elif order_id == position.get("sl_order_id"):
                # стоп-лосс сработал — полностью закрываем позицию
                self.close_position(position, reason="SL")

        elif status == "PartiallyFilled":
            filled_qty = float(order_data.get("cumExecQty", 0))
            logging.info(f"Order {order_id} partially filled: {filled_qty}")

    def wait_for_order_filled(self, order_id, symbol, max_attempts=15, delay=2):
        for attempt in range(max_attempts):
            try:
                order_status = self.client.http_client.get_open_orders(
                    category="linear",
                    symbol=symbol,
                    orderId=order_id
                )
                logging.debug(
                    f"Attempt {attempt+1}: Order status response: {order_status}")
                if order_status.get("retCode") != 0:
                    logging.error(f"API error: {order_status.get('retMsg')}")
                    continue

                orders_list = order_status.get("result", {}).get("list", [])
                if not orders_list:
                    # Ордер может быть уже исполнен, проверяем историю
                    order_history = self.client.http_client.get_order_history(
                        category="linear",
                        symbol=symbol,
                        orderId=order_id
                    )
                    if order_history.get("retCode") == 0 and order_history.get("result", {}).get("list", []):
                        status = order_history["result"]["list"][0]["orderStatus"]
                        if status == "Filled":
                            logging.info(f"Ордер {order_id} исполнен")
                            return True
                        elif status in ["Rejected", "Cancelled"]:
                            logging.error(f"Ордер {order_id} был {status}")
                            return False
                    logging.warning(
                        f"Нет данных по ордеру {order_id} (список пуст).")
                    continue

                status = orders_list[0].get("orderStatus")
                if status == "Filled":
                    logging.info(f"Ордер {order_id} исполнен")
                    return True
                elif status == "PartiallyFilled":
                    logging.info(
                        f"Ордер {order_id} частично исполнен, продолжаем ожидание…")
                    continue
                elif status in ["Rejected", "Cancelled"]:
                    logging.error(f"Ордер {order_id} был {status}")
                    return False

            except Exception as e:
                logging.error(f"Исключение: {e}")

            time.sleep(delay)

        logging.warning(
            f"Ордер {order_id} не исполнен после {max_attempts} попыток")
        return False
