# trading_state.py
import datetime
from pybit.unified_trading import HTTP


class TradingState:
    def __init__(self, api_key, api_secret, symbol="BTCUSDT"):
        self.client = HTTP(api_key=api_key, api_secret=api_secret)
        self.symbol = symbol
        self.trading_start_time = None

    def start_trading(self):
        """Устанавливает время начала торговли."""
        self.trading_start_time = datetime.datetime.utcnow()

    def reset_trading_stats(self):
        """Сбрасывает время начала торговли."""
        self.trading_start_time = None

    def get_closed_pnl(self, limit=50):
        """Получает последние закрытые позиции и их PnL с биржи, начиная с trading_start_time."""
        try:
            if not self.trading_start_time:
                # Если start_time не установлен, можно либо вернуть пустой список,
                # либо сделать запрос без startTime
                return []

            start_time = int(self.trading_start_time.timestamp() * 1000)
            response = self.client.get_closed_pnl(
                category="linear",
                symbol=self.symbol,
                limit=limit,
                startTime=start_time
            )
            if response['retCode'] != 0:
                return []
            return response['result']['list']
        except Exception as e:
            print(f"Ошибка при получении статистики сделок: {e}")
            return []

    def get_trade_statistics(self):
        """Формирует статистику по закрытым сделкам."""
        trades = self.get_closed_pnl()
        total_profit = sum(float(t["closedPnl"])
                           for t in trades if float(t["closedPnl"]) > 0)
        total_loss = sum(float(t["closedPnl"])
                         for t in trades if float(t["closedPnl"]) < 0)
        total_pnl = total_profit + total_loss
        profitable_trades = len(
            [t for t in trades if float(t["closedPnl"]) > 0])
        total_trades = len(trades)

        return {
            "total_profit": total_profit,
            "total_loss": total_loss,
            "total_pnl": total_pnl,
            "total_trades": total_trades,
            "profitable_trades": profitable_trades,
            "recent_trades": trades[:50]  # последние 5 сделок
        }

    def get_trading_duration(self):
        """Возвращает время с начала торговли."""
        if not self.trading_start_time:
            return "00:00:00"
        duration = datetime.datetime.utcnow() - self.trading_start_time
        hours, remainder = divmod(duration.total_seconds(), 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{int(hours):02}:{int(minutes):02}:{int(seconds):02}"

    def get_current_positions(self):
        """Получает текущие позиции с биржи через REST API."""
        try:
            response = self.client.get_positions(
                category="linear",
                symbol=self.symbol,
                settleCoin="USDT"
            )
            if response['retCode'] != 0:
                return f"Ошибка при получении позиций: {response.get('retMsg', 'Нет сообщения об ошибке')}"

            positions = response['result']['list']
            if not positions:
                return "Нет открытых позиций."

            info = "Текущие позиции:\n"
            for pos in positions:
                if pos['symbol'] == self.symbol:
                    info += (
                        f"Символ: {pos['symbol']}\n"
                        f"Направление: {pos['side']}\n"
                        f"Цена входа: {pos['avgPrice']}\n"
                        f"Размер: {pos['size']}\n"
                        f"Нереализованный PnL: {pos['unrealisedPnl']}\n"
                        "----------------\n"
                    )
            return info.strip() if info.strip() != "Текущие позиции:" else "Нет открытых позиций по указанному символу."
        except Exception as e:
            print(f"Ошибка: {e}")
            return "Ошибка при получении позиций."
