import pandas as pd
import plotly.graph_objects as go
from trading_bot.market_analyzer import MarketAnalyzer
import trading_bot.config as config

import logging

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s",
    force=True
)


def plot_signals(df, analyzer):
    """
    Функция строит график на основе исторических 5m свечей (df) 
    и сигналов, сгенерированных анализатором (analyzer.generated_signals).
    Дополнительно отображаются горизонтальные линии для зон поддержки и сопротивления.
    """
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')

    fig = go.Figure()

    # Отрисовка 5m свечей
    fig.add_trace(
        go.Candlestick(
            x=df['timestamp'],
            open=df['open'],
            high=df['high'],
            low=df['low'],
            close=df['close'],
            name='Candles'
        )
    )

    for z in getattr(analyzer, 'zones_low', []):
        if z['active']:
            fig.add_hrect(y0=z['btm'], y1=z['top'],
                          fillcolor="green", opacity=0.15,
                          line_width=0, annotation_text='Low-Zone')

    for z in getattr(analyzer, 'zones_high', []):
        if z['active']:
            fig.add_hrect(y0=z['btm'], y1=z['top'],
                          fillcolor="red", opacity=0.15,
                          line_width=0, annotation_text='High-Zone')

    # Отрисовка аннотаций и точек сигналов
    for s in analyzer.generated_signals:
        signal_date = pd.to_datetime(s['timestamp'], unit='ms')
        # Добавление аннотации с типом сигнала и направлением
        fig.add_annotation(
            x=signal_date,
            y=s['entry'],
            text=f"{s['type']} {s['direction']}",
            showarrow=True,
            arrowhead=1,
            font=dict(
                size=12, color='green' if s['direction'] == 'long' else 'red')
        )
        # Точка входа
        fig.add_trace(go.Scatter(
            x=[signal_date],
            y=[s['entry']],
            mode='markers',
            marker=dict(symbol='circle', size=10),
            name='Entry'
        ))
        # Точка Stop Loss
        fig.add_trace(go.Scatter(
            x=[signal_date],
            y=[s['sl']],
            mode='markers',
            marker=dict(symbol='x', size=10),
            name='Stop Loss'
        ))
        # Точки Take Profit 1 и 2
        fig.add_trace(go.Scatter(
            x=[signal_date],
            y=[s['tp1']],
            mode='markers',
            marker=dict(symbol='triangle-up', size=10),
            name='TP1'
        ))
        fig.add_trace(go.Scatter(
            x=[signal_date],
            y=[s['tp2']],
            mode='markers',
            marker=dict(symbol='triangle-down', size=10),
            name='TP2'
        ))

    fig.update_layout(
        title='Торговые сигналы (5m)',
        xaxis_rangeslider_visible=False,
        template='plotly_dark',
        showlegend=True
    )

    fig.write_image("signals_plot.png", width=7680, height=4320, scale=1)
    print("График сохранен в файл: signals_plot.png")


def test_analyzer():
    # Читаем исторические данные 5m свечей
    df_5m = pd.read_csv("historical_candles.csv")
    df_5m.sort_values("timestamp", ascending=True, inplace=True)
    df_5m.reset_index(drop=True, inplace=True)

    expected_cols = {"timestamp", "open", "high", "low", "close", "volume"}
    missing_cols = expected_cols - set(df_5m.columns)
    if missing_cols:
        print(f"В DataFrame отсутствуют столбцы: {missing_cols}")
        return

    # Создаем экземпляр нового MarketAnalyzer с конфигурацией
    analyzer = MarketAnalyzer(config.TRADING_CONFIG)

    signals = []
    for _, row in df_5m.iterrows():
        candle = {
            "timestamp": int(row["timestamp"]),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row["volume"]),
        }
        signal = analyzer.generate_signal(candle)
        logging.debug(
            f"Всего зон Low={len(getattr(analyzer, 'zones_low', []))}, "
            f"High={len(getattr(analyzer, 'zones_high', []))}"
        )

        if signal.get("direction"):
            signals.append(signal)
            logging.debug(f"✅ сигнал: {signal}")
        else:
            logging.debug(
                f"⛔ нет сигнала  ts={candle['timestamp']}  price={candle['close']}"
            )

    print(f"Найдено сигналов: {len(signals)}")
    if signals:
        print("Примеры сигналов:")
        for s in signals[:5]:
            print(s)
        pd.DataFrame(signals).to_csv("test_signals.csv", index=False)
        print("Сигналы сохранены в файл: test_signals.csv")

    # Строим график на основе 5m свечей и полученных сигналов
    plot_signals(df_5m, analyzer)


if __name__ == "__main__":
    test_analyzer()
