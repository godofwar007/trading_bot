import os
from dotenv import load_dotenv


load_dotenv()
BYBIT_API_KEY = os.getenv('BYBIT_API_KEY')
BYBIT_API_SECRET = os.getenv('BYBIT_API_SECRET')
SYMBOL = "BTCUSDT"
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
LOG_FILE = "trading.log"


TRADING_CONFIG = {
    # ***Объём***
    # Больше окно + более высокий порог → “высокий объём” реже,
    # значит меньше ложных сделок на плоском рынке.
    'volume_window': 25,          # было 25
    'volume_high_multiplier': 1.45,   # было 1.1
    # не критично, но поможет игнорировать «тонкие» свечи
    'volume_low_multiplier': 0.7,

    # ***Зоны поддержки/сопротивления***
    'swing_length': 20,
    'swing_area': 'Wick Extremity',
    'swing_filter': 'Count',
    'swing_filter_value': 3,

    # ***RSI***
    # Более короткий RSI даёт сигнал быстрее,
    # а более жёсткие уровни 80/20 отсекают “середину”.
    'rsi_period': 21,             # было 50
    'rsi_overbought': 65,         # было 70
    'rsi_oversold': 35,           # было 30

    # ***Риск / стоп***
    # Чуть‑дальше стоп‑лосс за зоной → меньше выбиваний шумом.
    'sl_adjustment': 1.2,         # было 1
    'sl_buffer': 0.004,            # было 0.003

    'ema_short_period': 20,
    'ema_long_period': 60,
    'adx_period': 14,
    'adx_threshold': 15,

    # Отмена перегретых сигналов
    'rsi_max_for_long': 70,   # если RSI выше – лонг‑сигнал отменяем
    'rsi_min_for_short': 30   # если RSI ниже – шорт‑сигнал отменяем
}
