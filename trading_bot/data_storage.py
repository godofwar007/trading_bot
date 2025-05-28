import csv
import os

CSV_FILENAME = "candles.csv"


def clear_candle_csv():
    """Очищает файл и записывает заголовок столбцов."""
    with open(CSV_FILENAME, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "open", "high",
                        "low", "close", "volume"])


def save_candle_to_csv(candle: dict):
    """Сохраняет свечу в конец файла CSV."""
    with open(CSV_FILENAME, 'a', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([
            candle.get("timestamp"),
            candle.get("open"),
            candle.get("high"),
            candle.get("low"),
            candle.get("close"),
            candle.get("volume")
        ])


def load_candles_from_csv() -> list:

    if not os.path.exists(CSV_FILENAME):
        return []
    candles = []
    with open(CSV_FILENAME, 'r', newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row:
                continue
            candle = {
                "timestamp": int(row["timestamp"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
                "confirm": True  # Чтобы анализатор воспринимал это как "закрытую свечу"
            }
            candles.append(candle)
    # Сортируем свечи по возрастанию времени (от старой к новой)
    candles.sort(key=lambda c: c["timestamp"])
    return candles
