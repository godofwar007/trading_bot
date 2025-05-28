import numpy as np
from typing import List, Dict, Optional
from .position_manager import PositionManager

import logging


class PatternDetector:
    """Класс для обнаружения свечных паттернов (бычьих и медвежьих) на основе последних свечей."""

    def __init__(self):
        self.candles: List[Dict] = []

    def update(self, candle: Dict):
        self.candles.append(candle)
        if len(self.candles) > 5:
            self.candles.pop(0)

    def detect_pattern(self) -> Optional[Dict]:
        pass


class ZoneBuilder:
    def __init__(self, daily_candles: List[Dict], tolerance: float = 0.005):
        self.tolerance = tolerance
        # внутренние списки — словари {level, touches}
        self._support_zones: List[Dict[str, float | int]] = []
        self._resistance_zones: List[Dict[str, float | int]] = []
        if daily_candles:
            self._build_initial_zones(daily_candles)

    @property
    def support_zones(self) -> List[float]:
        """Возвращает список уровней поддержки для backward-совместимости"""
        return [z['level'] for z in self._support_zones]

    @property
    def resistance_zones(self) -> List[float]:
        """Возвращает список уровней сопротивления для backward-совместимости"""
        return [z['level'] for z in self._resistance_zones]

    def _build_initial_zones(self, daily_candles: List[Dict]):
        pass

    def update_zones(self, candle: Dict):
        price_high = candle['high']
        price_low = candle['low']
        # пробой сопротивления
        if self._resistance_zones:
            highest = max(self._resistance_zones,
                          key=lambda z: z['level'])['level']
            if price_high > highest * (1 + self.tolerance):
                self._resistance_zones.append(
                    {'level': price_high, 'touches': 0})
        else:
            self._resistance_zones.append({'level': price_high, 'touches': 0})

        # пробой поддержки
        if self._support_zones:
            lowest = min(self._support_zones,
                         key=lambda z: z['level'])['level']
            if price_low < lowest * (1 - self.tolerance):
                self._support_zones.append({'level': price_low, 'touches': 0})
        else:
            self._support_zones.append({'level': price_low, 'touches': 0})

        # фильтрация близких уровней
        self._resistance_zones = self._filter_close_levels(
            self._resistance_zones)
        self._support_zones = self._filter_close_levels(self._support_zones)

        # подсчёт касаний по цене закрытия
        self._count_touches(candle['close'])

    def _count_touches(self, price: float):
        for z in self._support_zones:
            if abs(price - z['level'])/z['level'] <= self.tolerance:
                z['touches'] += 1
        for z in self._resistance_zones:
            if abs(price - z['level'])/z['level'] <= self.tolerance:
                z['touches'] += 1

    def is_near_zone(self, price: float, min_touches: int = 0) -> Optional[str]:
        for z in self._support_zones:
            if z['touches'] >= min_touches and abs(price - z['level'])/z['level'] <= self.tolerance:
                return 'support'
        for z in self._resistance_zones:
            if z['touches'] >= min_touches and abs(price - z['level'])/z['level'] <= self.tolerance:
                return 'resistance'
        return None


class VolumeAnalyzer:
    def __init__(self, window: int = 20, high_multiplier: float = 1.5, low_multiplier: float = 0.5):
        self.window = window
        self.high_mult = high_multiplier
        self.low_mult = low_multiplier
        self.volume_history: List[float] = []

    def update(self, volume: float):
        self.volume_history.append(volume)
        if len(self.volume_history) > self.window:
            self.volume_history.pop(0)

    def is_high_volume(self, volume: Optional[float] = None) -> bool:
        if len(self.volume_history) < self.window:
            return False
        vol = volume if volume is not None else self.volume_history[-1]
        avg = np.mean(self.volume_history)
        return vol > avg * self.high_mult

    def is_low_volume(self, volume: Optional[float] = None) -> bool:
        if len(self.volume_history) < self.window:
            return False
        vol = volume if volume is not None else self.volume_history[-1]
        avg = np.mean(self.volume_history)
        return vol < avg * self.low_mult


class RSIIndicator:
    def __init__(self, period: int = 14, overbought_level: float = 70, oversold_level: float = 30):
        self.period = period
        self.overbought = overbought_level
        self.oversold = oversold_level
        self.close_history: List[float] = []
        self.last_rsi: Optional[float] = None

    def update(self, close: float):
        self.close_history.append(close)
        if len(self.close_history) > self.period + 1:
            self.close_history.pop(0)
        if len(self.close_history) >= self.period + 1:
            self.last_rsi = self._calculate_rsi()
        else:
            self.last_rsi = None

    def _calculate_rsi(self) -> float:
        diffs = np.diff(self.close_history)
        gains = np.maximum(diffs, 0)
        losses = np.abs(np.minimum(diffs, 0))
        avg_gain = np.mean(gains[-self.period:])
        avg_loss = np.mean(losses[-self.period:])
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def is_overbought(self) -> bool:
        return self.last_rsi >= self.overbought if self.last_rsi is not None else False

    def is_oversold(self) -> bool:
        return self.last_rsi <= self.oversold if self.last_rsi is not None else False


class SignalValidator:
    def __init__(self,
                 config: Dict,
                 zone_builder: ZoneBuilder,
                 volume_analyzer: VolumeAnalyzer,
                 rsi_indicator: RSIIndicator):
        self.config = config
        self.zone_builder = zone_builder
        self.volume_analyzer = volume_analyzer
        self.rsi_indicator = rsi_indicator

    def validate(self, pattern: Optional[Dict], candle: Dict, prev_candle: Optional[Dict]) -> Optional[Dict]:
        # Требуем наличие паттерна
        if not pattern:
            return None

        rsi_val = self.rsi_indicator.last_rsi
        # ---   ГУШИМ 'экстремальные' сделки   -------------
        max_long = self.config.get(
            'rsi_max_for_long', self.rsi_indicator.overbought)
        min_short = self.config.get(
            'rsi_min_for_short', self.rsi_indicator.oversold)

        if pattern['direction'] == 'bullish' and rsi_val is not None and rsi_val >= max_long:
            return None        # RSI слишком высок – отказываемся от лонга
        if pattern['direction'] == 'bearish' and rsi_val is not None and rsi_val <= min_short:
            return None        # RSI слишком низок – отказываемся от шорта

        close_price = candle['close']
        zone_type = self.zone_builder.is_near_zone(close_price)
        is_high_vol = self.volume_analyzer.is_high_volume(candle['volume'])
        is_overbought = self.rsi_indicator.is_overbought()
        is_oversold = self.rsi_indicator.is_oversold()

        if pattern['direction'] == 'bullish':
            # 1) Отскок от поддержки – нужен либо RSI-oversold, либо повышенный объём
            if zone_type == 'support' and (is_oversold or is_high_vol):
                return {
                    'direction': 'long',
                    'type': pattern['name'],
                    'timestamp': candle['timestamp']
                }
            # 2) Пробой сопротивления – объёма по-прежнему достаточно
            elif zone_type == 'resistance' and is_high_vol:
                return {
                    'direction': 'long',
                    'type': pattern['name'],
                    'timestamp': candle['timestamp']
                }

        elif pattern['direction'] == 'bearish':
            # 1) Отскок от сопротивления – нужен RSI-overbought ИЛИ high-vol
            if zone_type == 'resistance' and (is_overbought or is_high_vol):
                return {
                    'direction': 'short',
                    'type': pattern['name'],
                    'timestamp': candle['timestamp']
                }
            # 2) Пробой поддержки – оставляем прежний фильтр по объёму
            elif zone_type == 'support' and is_high_vol:
                return {
                    'direction': 'short',
                    'type': pattern['name'],
                    'timestamp': candle['timestamp']
                }

        return None


class MarketAnalyzer:
    def __init__(self, config: Dict, position_manager: PositionManager | None = None):
        self.config = config
        daily_candles = config.get('daily_candles', [])
        self.zone_builder = ZoneBuilder(
            daily_candles, config.get('zone_tolerance', 0.005))
        self.volume_analyzer = VolumeAnalyzer(
            config.get('volume_window', 20),
            config.get('volume_high_multiplier', 1.5),
            config.get('volume_low_multiplier', 0.5)
        )
        self.rsi_indicator = RSIIndicator(
            config.get('rsi_period', 14),
            config.get('rsi_overbought', 70),
            config.get('rsi_oversold', 30)
        )
        self.pattern_detector = PatternDetector()
        self.signal_validator = SignalValidator(
            self.config, self.zone_builder, self.volume_analyzer,
            self.rsi_indicator)
        self.generated_signals: List[Dict] = []
        self.prev_candle: Optional[Dict] = None
        self.position_manager = position_manager or PositionManager()
        self.atr_indicator = ATRIndicator(config.get('atr_period', 14))
        self.trend_filter = TrendFilter(
            config.get('ema_short_period', 20),
            config.get('ema_long_period', 60),
            config.get('adx_period', 14),
            config.get('adx_threshold', 15)
        )

    def _calculate_tp_levels(self, entry: float, sl: float, direction: str) -> Dict:
        # Вычисляем риск с учётом направления
        if direction == 'long':
            risk = entry - sl
            tp1 = entry + risk
            tp2 = entry + 1.5 * risk
        else:  # short
            risk = sl - entry
            tp1 = entry - risk
            tp2 = entry - 1.5 * risk

        # Если риск превышает 2% от цены входа, сигнал не принимается
        if risk / entry > 0.02:
            return {}

        return {'tp1': tp1, 'tp2': tp2}

    def _calculate_stop_loss(self, entry: float, base_sl: float, direction: str) -> float:
        adjustment = self.config.get('sl_adjustment', 1.0)
        if direction == 'long':
            return entry - (entry - base_sl) * adjustment
        else:
            return entry + (base_sl - entry) * adjustment

    def generate_signal(self, candle: Dict) -> Dict:
        try:
            # Преобразование данных свечи
            for key in ['open', 'close', 'high', 'low', 'volume']:
                candle[key] = float(candle[key])
        except ValueError as e:
            logging.error(f"Ошибка преобразования данных свечи: {e}")
            return {}

        # Обновление индикаторов и зон
        self.pattern_detector.update(candle)
        self.volume_analyzer.update(candle['volume'])
        self.rsi_indicator.update(candle['close'])
        self.zone_builder.update_zones(candle)
        self.atr_indicator.update(
            candle, self.prev_candle['close'] if self.prev_candle else candle['close'])
        self.trend_filter.update(candle)

        pass


class ATRIndicator:
    def __init__(self, period=14):
        self.period = period
        self.tr_history = []
        self.last_atr = None

    def update(self, candle, prev_close):
        tr = max(candle['high'] - candle['low'],
                 abs(candle['high'] - prev_close),
                 abs(candle['low'] - prev_close))
        self.tr_history.append(tr)
        if len(self.tr_history) > self.period:
            self.tr_history.pop(0)
        if len(self.tr_history) == self.period:
            self.last_atr = np.mean(self.tr_history)


class TrendFilter:
    def __init__(self, short_period: int = 50, long_period: int = 200, adx_period: int = 14, adx_threshold: float = 25):
        self.short_period = short_period
        self.long_period = long_period
        self.adx_period = adx_period
        self.adx_threshold = adx_threshold
        self.close_history: List[float] = []
        self.tr_list: List[float] = []
        self.dm_plus_list: List[float] = []
        self.dm_minus_list: List[float] = []
        self.dx_list: List[float] = []
        self.last_ema_short: Optional[float] = None
        self.last_ema_long: Optional[float] = None
        self.last_adx: Optional[float] = None
        self.prev_close: Optional[float] = None
        self.prev_high: Optional[float] = None
        self.prev_low: Optional[float] = None

    def update(self, candle: Dict) -> None:
        pass
