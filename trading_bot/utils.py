import logging
import requests
import random

from .config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, LOG_FILE


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()]
)


def send_telegram_message(message):
    """
    Формирует текст и отправляет его в Telegram.
    Порядок проверок важен: сначала закрытия / частичные закрытия,
    затем новые позиции — чтобы не путать сообщения.
    """
    text = ""

    if isinstance(message, dict):
        if 'position_closed' in message:
            pos = message['position']
            status_emoji = "🟢" if pos['profit'] > 0 else "🔴"
            mood_emojis = ["🎉", "🏆", "💪"] if pos['profit'] > 0 else [
                "😢", "⚠️", "📉"]
            intro = random.choice([
                f"{status_emoji} Сделка закрыта! {status_emoji}",
                f"{random.choice(mood_emojis)} Закрыли позицию",
                f"{status_emoji} Финал — сделка завершена"
            ])
            outro = random.choice(
                ["🚀 Вперед к новым вершинам!", "😊 Отличная работа!", "🔄 Готовимся к следующей!"])
            text = (
                f"{intro}\n"
                f"Причина: {pos['close_reason']}\n"
                f"Направление: {pos['direction'].upper()}\n"
                f"Вход → Выход: {pos['entry']:.2f} → {pos['close_price']:.2f}\n"
                f"Прибыль: {pos['profit']:.2f} USDT\n"
                f"{outro}"
            )

        # TP1 2\3
        elif 'position_partially_closed' in message:
            pos = message['position']
            intro = random.choice([
                "🟡 Половина позиции зафиксирована!",
                "✂️ 50% позиции резанули",
                "🔔 Частичное закрытие прошло успешно"
            ])
            outro = random.choice([
                "🔄 Осталось 50% – держим курс",
                "⚙️ SL обновлён, готовы к движению",
                "📊 Дальше — больше!"
            ])
            text = (
                f"{intro}\n"
                f"Символ: {pos.get('symbol', 'N/A')}\n"
                f"Направление: {pos['direction'].upper()}\n"
                f"Закрыто: 50% позиции\n"
                f"Осталось: {pos['qty']:.4f}\n"
                f"Новый SL: {pos['sl']:.2f}\n"
                f"{outro}"
            )

        # полное закрытие TP
        elif 'position_tp' in message:
            pos = message['position']
            intro = random.choice([
                "🟢 Take-Profit выполнен!",
                "🏁 Закрыли позицию по TP",
                "💰 Фиксация прибыли"
            ])
            outro = random.choice([
                "🚀 Продолжаем в том же духе!",
                "🎯 Взяли профит!",
                "📈 Отличный выход"
            ])
            text = (
                f"{intro}\n"
                f"Символ: {pos.get('symbol', 'N/A')}\n"
                f"Направление: {pos['direction'].upper()}\n"
                f"Вход → Выход: {pos['entry']:.2f} → {pos['close_price']:.2f}\n"
                f"Прибыль: {pos['profit']:.2f} USDT\n"
                f"{outro}"
            )
            try:
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                payload = {"chat_id": TELEGRAM_CHAT_ID,
                           "text": text, "parse_mode": "HTML"}
                requests.post(url, json=payload).raise_for_status()
                logging.info(f"Telegram TP message sent: {text}")
            except Exception as e:
                logging.error(f"Ошибка при отправке TP-уведомления: {e}")
            return

        # single-TPновое открытие
        elif 'position_single' in message:
            pos = message['position_single']
            intro = random.choice([
                "🆕 Открыта новая позиция!",
                "🚀 Входим в сделку — поехали!",
                "🌟 Новая точка входа"
            ])
            outro = random.choice([
                "🤞 Удачи!",
                "🏹 Нацелен на профит",
                "🔍 Слежу за ситуацией"
            ])
            text = (
                f"{intro}\n"
                f"Символ: {pos.get('symbol', 'N/A')}\n"
                f"Направление: {pos['direction']}\n"
                f"Вход: {pos['entry']:.2f}\n"
                f"Объём: {pos['qty']:.4f}\n"
                f"SL: {pos['sl']:.2f}\n"
                f"TP: {pos['tp']:.2f}\n"
                f"{outro}"
            )
            try:
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                payload = {"chat_id": TELEGRAM_CHAT_ID,
                           "text": text, "parse_mode": "HTML"}
                requests.post(url, json=payload).raise_for_status()
                logging.info(f"Telegram single‐TP message sent: {text}")
            except Exception as e:
                logging.error(
                    f"Ошибка при отправке уведомления single‐TP: {e}")
            return

        # новая позиция
        elif 'position' in message:
            pos = message['position']
            intro = random.choice([
                "🆕 Открыта новая позиция!",
                "🚀 Входим в сделку — поехали!",
                "🌟 Новая точка входа"
            ])
            outro = random.choice(
                ["🤞 Удачи!", "🏹 Нацелен на профит", "🔍 Слежу за ситуацией"])
            text = (
                f"{intro}\n"
                f"Символ: {pos.get('symbol', 'N/A')}\n"
                f"Направление: {pos['direction'].upper()}\n"
                f"Вход: {pos['entry']:.2f}\n"
                f"Объём: {pos['qty']:.4f}\n"
                f"SL: {pos['sl']:.2f}\n"
                f"TP1: {pos['tp1']:.2f} | TP2: {pos['tp2']:.2f}\n"
                f"{outro}"
            )

        # прочeе
        else:
            text = str(message)

    else:
        text = message

    response = None
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML"
        }
        response = requests.post(url, json=payload)
        response.raise_for_status()
        logging.info(f"Telegram message sent: {text}")
    except requests.exceptions.RequestException as e:
        status = response.status_code if response is not None else 'N/A'
        body = response.text if response is not None else 'N/A'
        logging.error(
            f"Telegram error: {e}, HTTP status: {status}, Response: {body}")
