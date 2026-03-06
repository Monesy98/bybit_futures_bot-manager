import pandas as pd
import pandas_ta as ta
from core.strategy import BaseStrategy

class ChandelierExitStrategy(BaseStrategy):
    def __init__(self, config):
        super().__init__(config)
        # Параметры
        self.length = config.get('length', 22)
        self.multiplier = config.get('multiplier', 3.0)
        self.use_close = config.get('use_close', True)   # если True – экстремумы по close, иначе по high/low

        # Хранилища для Heiken Ashi свечей
        self.ha_candles = []          # список HA свечей в формате [timestamp, open, high, low, close]
        self.prev_ha_open = None
        self.prev_ha_close = None

        # Для Chandelier
        self.long_stop = None
        self.short_stop = None
        self.prev_long_stop = None
        self.prev_short_stop = None
        self.dir = 1
        self.prev_dir = 1

    def _heiken_ashi(self, candle):
        """Преобразование обычной свечи в Heiken Ashi"""
        timestamp, open_, high, low, close, _ = candle
        if not self.ha_candles:
            # Первая HA свеча
            ha_close = (open_ + high + low + close) / 4
            ha_open = (open_ + close) / 2
        else:
            ha_close = (open_ + high + low + close) / 4
            ha_open = (self.prev_ha_open + self.prev_ha_close) / 2
        ha_high = max(high, ha_open, ha_close)
        ha_low = min(low, ha_open, ha_close)
        self.prev_ha_open = ha_open
        self.prev_ha_close = ha_close
        return [timestamp, ha_open, ha_high, ha_low, ha_close]

    async def on_candle(self, candle):
        # 1. Преобразуем свечу в Heiken Ashi
        ha_candle = self._heiken_ashi(candle)
        self.ha_candles.append(ha_candle)
        if len(self.ha_candles) > self.length + 2:
            self.ha_candles.pop(0)

        # Недостаточно данных для расчёта
        if len(self.ha_candles) < self.length + 1:
            return None

        # Создаём DataFrame из Heiken Ashi свечей
        df = pd.DataFrame(self.ha_candles, columns=['timestamp', 'open', 'high', 'low', 'close'])

        # 2. Расчёт ATR на Heiken Ashi свечах
        atr_series = ta.atr(df['high'], df['low'], df['close'], length=self.length, mamode='rma')
        atr = atr_series.iloc[-1] * self.multiplier

        # 3. Экстремумы за length периодов на HA свечах
        lookback = df.tail(self.length)
        if self.use_close:
            highest = lookback['close'].max()
            lowest = lookback['close'].min()
        else:
            highest = lookback['high'].max()
            lowest = lookback['low'].min()

        # 4. Расчёт сырых стопов
        raw_long_stop = highest - atr
        raw_short_stop = lowest + atr

        # 5. Инициализация предыдущих стопов
        if self.prev_long_stop is None:
            self.prev_long_stop = raw_long_stop
            self.prev_short_stop = raw_short_stop
            self.long_stop = raw_long_stop
            self.short_stop = raw_short_stop
            return None

        # 6. Сглаживание стопов с учётом предыдущего закрытия HA
        prev_ha_close = df['close'].iloc[-2]   # close[1] в терминах Pine

        if prev_ha_close > self.prev_long_stop:
            self.long_stop = max(raw_long_stop, self.prev_long_stop)
        else:
            self.long_stop = raw_long_stop

        if prev_ha_close < self.prev_short_stop:
            self.short_stop = min(raw_short_stop, self.prev_short_stop)
        else:
            self.short_stop = raw_short_stop

        # 7. Определение направления на HA close
        current_ha_close = df['close'].iloc[-1]
        new_dir = self.dir
        if current_ha_close > self.prev_short_stop:
            new_dir = 1
        elif current_ha_close < self.prev_long_stop:
            new_dir = -1
        else:
            new_dir = self.dir

        self.prev_dir = self.dir
        self.dir = new_dir

        # 8. Сигнал при смене направления
        signal = None
        if self.dir == 1 and self.prev_dir == -1:
            signal = 'buy'
        elif self.dir == -1 and self.prev_dir == 1:
            signal = 'sell'

        # Сохраняем текущие стопы для следующей итерации
        self.prev_long_stop = self.long_stop
        self.prev_short_stop = self.short_stop

        return signal