import logging
import asyncio
import datetime
import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes
)
from .config import TELEGRAM_BOT_TOKEN, TRADING_CONFIG
from . import config
from . import subscribe
from .market_analyzer import MarketAnalyzer
from .bybit_client import BybitClient
from . import data_storage
from .position_manager import PositionManager
from .trading_state import TradingState
from .config import BYBIT_API_KEY, BYBIT_API_SECRET
from pybit.unified_trading import HTTP
import os
from dotenv import load_dotenv


load_dotenv()
AUTHORIZED_USERS = os.getenv('AUTHORIZED_USERS')
AUTHORIZED_USERS = [int(u) for u in AUTHORIZED_USERS.split(
    ',')] if AUTHORIZED_USERS else []

# Глобальные переменные
TRADING_ACTIVE = False
TRADING_TASK = None
SELECTED_SYMBOL = "BTCUSDT"
LEVERAGE = 1
POSITION_NOTIONAL = 1  # по умолчанию
MIN_BALANCE = None
AUTO_STOP_ENABLED = False
TRADE_HISTORY = []

# переменная, чтобы "ловить" ввод пользователя
AWAITING_SIZE_INPUT = False

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

HTTP_CLIENT = HTTP(
    testnet=False,                    # или True для тестнета
    api_key=BYBIT_API_KEY,
    api_secret=BYBIT_API_SECRET
)

# Инициализация клиента Bybit
bybit_client = BybitClient()
position_manager = PositionManager()
trading_state = TradingState(
    api_key=BYBIT_API_KEY, api_secret=BYBIT_API_SECRET, symbol=SELECTED_SYMBOL)


def check_authorized(user_id: int) -> bool:
    return user_id in AUTHORIZED_USERS


def get_monthly_metrics(symbol: str) -> tuple[float, int]:
    """
    PnL и кол‑во закрытых сделок c 00:00 UTC 1‑го числа
    до текущего момента. Разбиваем период на интервалы ≤ 7 суток.
    """
    now = datetime.datetime.utcnow()
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    total_pnl = 0.0
    total_trades = 0

    chunk_start = start
    seven_days = datetime.timedelta(days=7)

    while chunk_start < now:
        chunk_end = min(chunk_start + seven_days, now)

        resp = HTTP_CLIENT.get_closed_pnl(
            category="linear",
            symbol=symbol,
            startTime=int(chunk_start.timestamp() * 1000),
            endTime=int(chunk_end.timestamp() * 1000),
            limit=1000
        )

        rows = resp.get("result", {}).get("list", [])
        total_pnl += sum(float(r["closedPnl"]) for r in rows)
        total_trades += len(rows)

        chunk_start = chunk_end + datetime.timedelta(milliseconds=1)

    return total_pnl, total_trades


def get_server_time() -> int:
    """
    Получаем серверное время Bybit (в мс).
    Возвращаем как int, чтобы потом использовать в запросах.
    """
    r = requests.get("https://api.bybit.com/v5/market/time")
    server_ts = r.json()["time"]
    return int(server_ts)


def get_balance() -> float:
    """Получение баланса с единого торгового аккаунта."""
    try:

        balance_info = bybit_client.get_unified_wallet_balance()

        if balance_info.get("retCode") == 0:
            return float(balance_info["result"]["list"][0]["totalEquity"])
        else:
            logging.error(
                f"Не удалось получить баланс: {balance_info.get('retMsg')}")
            return 0.0
    except Exception as e:
        logging.error(f"Ошибка при получении баланса: {e}")
        return 0.0


def get_trade_report(days: int = None) -> str:
    """Формирование отчета по сделкам."""
    if not TRADE_HISTORY:
        return "Сделок пока нет."

    if days:
        now = datetime.datetime.utcnow()
        cutoff = now - datetime.timedelta(days=days)
        period_trades = [t for t in TRADE_HISTORY if t["time"] >= cutoff]
        return f"Сделок за последние {days} дней: {len(period_trades)}"

    total_trades = len(TRADE_HISTORY)
    wins = sum(1 for t in TRADE_HISTORY if t["profit"] > 0)
    profit = sum(t["profit"] for t in TRADE_HISTORY)
    return (
        f"Всего сделок: {total_trades}\n"
        f"Прибыльных: {wins}\n"
        f"Общий PnL: {profit:.2f} USDT"
    )


async def trading_loop():
    import logging
    global TRADING_ACTIVE, MIN_BALANCE, AUTO_STOP_ENABLED, position_manager

    # 1) Настройка символа и анализатора
    config.SYMBOL = SELECTED_SYMBOL
    subscribe.SYMBOL = SELECTED_SYMBOL
    subscribe.analyzer = MarketAnalyzer(
        TRADING_CONFIG,
        position_manager=position_manager
    )
    position_manager = subscribe.analyzer.position_manager

    # 2) Очищаем файл CSV, чтобы сохранить новую историю
    data_storage.clear_candle_csv()
    client = BybitClient()

    # 3) Загружаем последние 100 свечей
    historical_candles = client.get_historical_kline(
        symbol=SELECTED_SYMBOL,
        limit=1200,
        interval="5"
    )
    logging.info(
        f"Загружено {len(historical_candles)} свечей для инициализации.")

    historical_candles.sort(key=lambda c: c["timestamp"])
    for c in historical_candles:
        data_storage.save_candle_to_csv(c)
        subscribe.analyzer.generate_signal(c)

    # 5) Основной цикл: пока TRADING_ACTIVE = True, пытаемся подключиться к WebSocket
    while TRADING_ACTIVE:
        try:
            async with subscribe.websockets.connect("wss://stream.bybit.com/v5/public/linear") as ws:
                await subscribe.subscribe(ws)
                # Запускаем heartbeat в фоне
                heartbeat_task = asyncio.create_task(
                    subscribe.send_heartbeat(ws))

                # Цикл чтения свечей из WebSocket
                while TRADING_ACTIVE:
                    # Проверка авто-стопа по балансу
                    if AUTO_STOP_ENABLED and MIN_BALANCE is not None:
                        balance = get_balance()
                        if balance <= MIN_BALANCE:
                            logging.info(
                                "Баланс ниже минимального, остановка торговли.")
                            TRADING_ACTIVE = False
                            break

                    # Получаем следующую свечу
                    raw_data = await ws.recv()
                    data = subscribe.json.loads(raw_data)

                    # Обрабатываем свечу, если это закрытая свеча (confirm=True)
                    if data.get("data") and data["data"][0].get("confirm"):
                        candle = data["data"][0]
                        logging.info(f"Получена новая свеча: {candle}")

                        # Сохраняем свечу в CSV
                        data_storage.save_candle_to_csv(candle)

                        # Генерируем сигнал
                        signal = subscribe.analyzer.generate_signal(candle)
                        if signal.get("direction"):
                            logging.info(
                                "Открытие позиции, т.к. сигнал сгенерирован.")
                            position_manager.open_position(
                                signal,
                                leverage=LEVERAGE,
                                position_notional=POSITION_NOTIONAL,
                                symbol=SELECTED_SYMBOL
                            )
                        else:
                            logging.info(
                                "Позиция не открывается – сигнал отсутствует.")
        except Exception as e:
            logging.error(f"Ошибка в торговом цикле: {e}")
            if 'heartbeat_task' in locals() and not heartbeat_task.cancelled():
                heartbeat_task.cancel()
            await asyncio.sleep(5)

    logging.info("Торговля остановлена. Выходим из trading_loop.")


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not check_authorized(update.effective_user.id):
        await update.message.reply_text("Нет прав доступа.")
        return

    keyboard = [
        [InlineKeyboardButton("Торговля", callback_data="trade_menu")],
        [InlineKeyboardButton("Настройки", callback_data="settings_menu")],
        [InlineKeyboardButton("Статистика", callback_data="stats_menu")],
        [InlineKeyboardButton("Позиции", callback_data="positions_menu")]
    ]
    await update.message.reply_text(
        "SMC Trading Bot\nВыберите раздел:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not check_authorized(query.from_user.id):
        await query.answer("Нет прав доступа.")
        return

    await query.answer()
    data = query.data
    global TRADING_ACTIVE, TRADING_TASK, SELECTED_SYMBOL, LEVERAGE
    global POSITION_NOTIONAL, MIN_BALANCE, AUTO_STOP_ENABLED, AWAITING_SIZE_INPUT

    # Главное меню

    if data == "trade_menu":
        if TRADING_ACTIVE:
            month_pnl, month_trades = get_monthly_metrics(SELECTED_SYMBOL)

            status = (
                "🟢 Торговля активна\n"
                f"Время с запуска: {trading_state.get_trading_duration()}\n"
                f"Закрытых сделок (с начала месяца): {month_trades}\n"
                f"📆 PnL с начала месяца: "
                f"{'+' if month_pnl >= 0 else ''}{month_pnl:.2f}$"
            )
        else:
            status = "⛔️ Торговля отключена"

        tp_mode_label = "TP режим: SL+TP" if position_manager.tp_mode == "single" \
                        else "TP режим: SL+TP1+TP2"

        keyboard = [
            [InlineKeyboardButton(
                "Запустить" if not TRADING_ACTIVE else "Остановить",
                callback_data="toggle_trading")],
            [InlineKeyboardButton(tp_mode_label,
                                  callback_data="toggle_tp_mode")],
            [InlineKeyboardButton("Выбрать символ",
                                  callback_data="choose_symbol")],
            [InlineKeyboardButton("Назад", callback_data="main_menu")]
        ]
        await query.edit_message_text(
            status, reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data == "toggle_tp_mode":
        new_mode = "single" if position_manager.tp_mode == "dual" else "dual"
        position_manager.set_tp_mode(new_mode)
        mode_text = "SL + TP" if new_mode == "single" else "SL + TP1 + TP2"
        await query.edit_message_text(f"Выбран режим: {mode_text}")

        keyboard = [[InlineKeyboardButton("Назад",
                                          callback_data="trade_menu")]]
        await query.message.reply_text("Вернуться:",
                                       reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "toggle_trading":
        TRADING_ACTIVE = not TRADING_ACTIVE
        if TRADING_ACTIVE:
            TRADING_TASK = asyncio.create_task(trading_loop())
            trading_state.start_trading()
            await query.edit_message_text("Торговля запущена")
        else:
            if TRADING_TASK:
                TRADING_TASK.cancel()
                TRADING_TASK = None
                trading_state.reset_trading_stats()

            trading_state.TRADING_START_TIME = None
            await query.edit_message_text("Торговля остановлена")
        keyboard = [[InlineKeyboardButton(
            "Назад", callback_data="trade_menu")]]
        await query.message.reply_text("Вернуться:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "choose_symbol":
        keyboard = [
            [InlineKeyboardButton("BTCUSDT", callback_data="symbol|BTCUSDT"),
             InlineKeyboardButton("ETHUSDT", callback_data="symbol|ETHUSDT")],
            [InlineKeyboardButton("SOLUSDT", callback_data="symbol|SOLUSDT"),
             InlineKeyboardButton("XRPUSDT", callback_data="symbol|XRPUSDT")],
            [InlineKeyboardButton("BNBUSDT", callback_data="symbol|BNBUSDT"),
             InlineKeyboardButton("LTCUSDT", callback_data="symbol|LTCUSDT")],
            [InlineKeyboardButton("ADAUSDT", callback_data="symbol|ADAUSDT"),
             InlineKeyboardButton("DOTUSDT", callback_data="symbol|DOTUSDT")],
            [InlineKeyboardButton("Назад", callback_data="trade_menu")]
        ]
        await query.edit_message_text(
            f"Текущий символ: {SELECTED_SYMBOL}\nВыберите новый:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data.startswith("symbol|"):
        SELECTED_SYMBOL = data.split("|")[1]
        trading_state.symbol = SELECTED_SYMBOL
        keyboard = [[InlineKeyboardButton(
            "Назад", callback_data="trade_menu")]]
        await query.edit_message_text(
            f"Выбран символ: {SELECTED_SYMBOL}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    # Меню настроек
    elif data == "settings_menu":
        balance = get_balance()
        min_bal_text = (
            f"Мин. баланс: {MIN_BALANCE}$" if AUTO_STOP_ENABLED and MIN_BALANCE is not None
            else "Мин. баланс: Выкл"
        )
        keyboard = [
            [InlineKeyboardButton(
                f"Баланс: {balance:.2f} USDT", callback_data="refresh_balance")],
            [InlineKeyboardButton(
                f"Плечо: {LEVERAGE}x", callback_data="set_leverage")],
            [InlineKeyboardButton(
                f"Размер: {POSITION_NOTIONAL}$", callback_data="set_size")],
            [InlineKeyboardButton(
                min_bal_text, callback_data="toggle_min_balance")],
            [InlineKeyboardButton("Установить мин. баланс",
                                  callback_data="set_min_balance")],
            [InlineKeyboardButton("Назад", callback_data="main_menu")]
        ]
        await query.edit_message_text("Настройки:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "refresh_balance":
        balance = get_balance()
        await query.edit_message_text(f"Текущий баланс: {balance:.2f} USDT")
        keyboard = [[InlineKeyboardButton(
            "Назад", callback_data="settings_menu")]]
        await query.message.reply_text("Вернуться:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "toggle_min_balance":
        AUTO_STOP_ENABLED = not AUTO_STOP_ENABLED
        status = "включен" if AUTO_STOP_ENABLED else "отключен"
        await query.edit_message_text(f"Авто-стоп по балансу {status}")
        keyboard = [[InlineKeyboardButton(
            "Назад", callback_data="settings_menu")]]
        await query.message.reply_text("Вернуться:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "set_min_balance":
        keyboard = [
            [InlineKeyboardButton("10$", callback_data="min_balance|10"),
             InlineKeyboardButton("50$", callback_data="min_balance|50")],
            [InlineKeyboardButton("100$", callback_data="min_balance|100"),
             InlineKeyboardButton("500$", callback_data="min_balance|500")],
            [InlineKeyboardButton("Назад", callback_data="settings_menu")]
        ]
        await query.edit_message_text(
            "Выберите минимальный баланс:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data.startswith("min_balance|"):
        MIN_BALANCE = float(data.split("|")[1])
        AUTO_STOP_ENABLED = True
        keyboard = [[InlineKeyboardButton(
            "Назад", callback_data="settings_menu")]]
        await query.edit_message_text(
            f"Установлен мин. баланс: {MIN_BALANCE}$",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data == "set_leverage":
        keyboard = [
            [InlineKeyboardButton("1x", callback_data="leverage|1"),
             InlineKeyboardButton("5x", callback_data="leverage|5"),
             InlineKeyboardButton("10x", callback_data="leverage|10")],
            [InlineKeyboardButton("20x", callback_data="leverage|20"),
             InlineKeyboardButton("30x", callback_data="leverage|30")],
            [InlineKeyboardButton("Назад", callback_data="settings_menu")]
        ]
        await query.edit_message_text("Выберите плечо:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("leverage|"):
        LEVERAGE = int(data.split("|")[1])
        keyboard = [[InlineKeyboardButton(
            "Назад", callback_data="settings_menu")]]
        await query.edit_message_text(
            f"Установлено плечо: {LEVERAGE}x",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data == "set_size":
        AWAITING_SIZE_INPUT = True
        await query.edit_message_text(
            "Введите желаемый размер позиции в USDT (минимум 0.5)."
        )
        keyboard = [[InlineKeyboardButton(
            "Назад", callback_data="settings_menu")]]
        await query.message.reply_text("Чтобы отменить, нажмите 'Назад'.",
                                       reply_markup=InlineKeyboardMarkup(keyboard))

    # Меню статистики
    elif data == "stats_menu":
        keyboard = [
            [InlineKeyboardButton("Общий отчет", callback_data="full_report")],
            [InlineKeyboardButton("За 24 часа",  callback_data="report_1d")],
            [InlineKeyboardButton("За 7 дней",   callback_data="report_7d")],
            [InlineKeyboardButton("За 30 дней",  callback_data="report_30d")],
            [InlineKeyboardButton("Назад",       callback_data="main_menu")]
        ]
        await query.edit_message_text(
            "Статистика сделок:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data == "full_report":
        # Получаем последние 50 сделок и сортируем по времени (от новых к старым)
        resp = HTTP_CLIENT.get_executions(
            category="linear",
            symbol=SELECTED_SYMBOL,
            limit=50
        )
        trades = resp["result"]["list"]
        trades.sort(key=lambda t: int(t["execTime"]), reverse=True)

        # Словарь для перевода сторон сделки
        side_map = {
            "BUY": "Покупка",
            "SELL": "Продажа"
        }

        report_lines = ["📊 Последние 50 сделок:"]
        for tr in trades:
            # Преобразуем UNIX‑метку в локальное время
            ts = datetime.datetime.fromtimestamp(int(tr["execTime"]) / 1000)
            date_str = ts.strftime("%d.%m.%Y %H:%M")
            side_str = side_map.get(
                tr["side"].upper(), tr["side"].capitalize())

            # Форматирование чисел: цена — с двумя знаками, объём — с четырьмя
            price = float(tr["execPrice"])
            qty = float(tr["execQty"])

            report_lines.append(
                f"• {date_str} — {side_str} {tr['symbol']}\n"
                f"    Цена: {price:,.2f} USDT; Объём: {qty:,.4f}"
            )

        report_text = "\n".join(report_lines)
        keyboard = [
            [InlineKeyboardButton("За 24 часа", callback_data="report_1d")],
            [InlineKeyboardButton("За 7 дней",  callback_data="report_7d")],
            [InlineKeyboardButton("За 30 дней", callback_data="report_30d")],
            [InlineKeyboardButton("Назад",       callback_data="main_menu")]
        ]
        await query.edit_message_text(
            report_text,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data == "report_1d":
        report = get_trade_report(1)
        keyboard = [[InlineKeyboardButton(
            "Назад", callback_data="stats_menu")]]
        await query.edit_message_text(report, reply_markup=InlineKeyboardMarkup(keyboard))
    elif data == "report_7d":
        report = get_trade_report(7)
        keyboard = [[InlineKeyboardButton(
            "Назад", callback_data="stats_menu")]]
        await query.edit_message_text(report, reply_markup=InlineKeyboardMarkup(keyboard))
    elif data == "report_30d":
        report = get_trade_report(30)
        keyboard = [[InlineKeyboardButton(
            "Назад", callback_data="stats_menu")]]
        await query.edit_message_text(report, reply_markup=InlineKeyboardMarkup(keyboard))

    # Меню позиций
    elif data == "positions_menu":
        positions = trading_state.get_current_positions()
        keyboard = []

        if position_manager.active_positions:
            keyboard.append([InlineKeyboardButton("Закрыть позицию",
                                                  callback_data="close_position")])

        keyboard.append([InlineKeyboardButton("Обновить", callback_data="positions_menu"),
                         InlineKeyboardButton("Назад",    callback_data="main_menu")])
        try:
            await query.edit_message_text(
                text=f"{positions}\n⌚{datetime.datetime.utcnow().strftime('%H:%M:%S')}",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except Exception as e:
            if "Message is not modified" in str(e):
                await query.answer("Данные актуальны")
            else:
                raise e

    elif data == "main_menu":
        keyboard = [
            [InlineKeyboardButton("Торговля", callback_data="trade_menu")],
            [InlineKeyboardButton("Настройки", callback_data="settings_menu")],
            [InlineKeyboardButton("Статистика", callback_data="stats_menu")],
            [InlineKeyboardButton("Позиции", callback_data="positions_menu")]
        ]
        await query.edit_message_text("Главное меню:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "close_position":
        if not position_manager.active_positions:
            await query.answer("Открытых позиций нет")
        else:
            # выполняем в отдельном потоке, чтобы не блокировать loop
            closed = await asyncio.to_thread(
                position_manager.market_close_active_position
            )
            await query.answer("✅ Позиция закрыта" if closed else
                               "❌ Не удалось закрыть позицию")

        positions = trading_state.get_current_positions()
        keyboard = [
            [InlineKeyboardButton("Обновить", callback_data="positions_menu"),
             InlineKeyboardButton("Назад",    callback_data="main_menu")]
        ]
        if position_manager.active_positions:
            keyboard.insert(0, [InlineKeyboardButton("Закрыть позицию",
                                                     callback_data="close_position")])

        await query.edit_message_text(
            text=f"{positions}\n⌚{datetime.datetime.utcnow().strftime('%H:%M:%S')}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return


async def handle_size_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обработчик текстовых сообщений от пользователя,
    который ожидается после нажатия 'set_size' (AWAITING_SIZE_INPUT = True).
    """
    global AWAITING_SIZE_INPUT, POSITION_NOTIONAL

    # Проверяем, что пользователь авторизован
    if not update.effective_user or not check_authorized(update.effective_user.id):
        return

    # Если мы не ждём ввода размера, игнорируем
    if not AWAITING_SIZE_INPUT:
        return

    # Пытаемся распарсить число
    text = update.message.text.strip()
    try:
        value = float(text)
        if value < 0.5:
            await update.message.reply_text("Минимально допустимый размер – 0.5 USDT.")
            return
        POSITION_NOTIONAL = value
        AWAITING_SIZE_INPUT = False
        current_price = bybit_client.get_current_price(SELECTED_SYMBOL)
        if not current_price:
            await update.message.reply_text("❌ Не удалось получить цену")
            return

        calculated_qty = (POSITION_NOTIONAL * LEVERAGE) / current_price
        balance_text = (
            f"Установлено: {POSITION_NOTIONAL}$\n"
            f"➗ С плечом {LEVERAGE}x → ~{calculated_qty:.4f} {SELECTED_SYMBOL}"
        )
        await update.message.reply_text(balance_text)
    except ValueError:
        await update.message.reply_text("Некорректный ввод. Введите число, например 0.5 или 12.3.")


def main():
    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start_command))

    application.add_handler(CallbackQueryHandler(handle_buttons))

    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, handle_size_input))

    application.run_polling()


if __name__ == "__main__":
    main()
