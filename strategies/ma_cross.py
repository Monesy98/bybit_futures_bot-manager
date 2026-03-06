import pandas as pd
import pandas_ta as ta
from core.strategy import BaseStrategy

class MovingAverageCross(BaseStrategy):
    def __init__(self, config):
        super().__init__(config)
        self.fast_period = config.get('fast_period', 9)
        self.slow_period = config.get('slow_period', 21)
        self.prices = []
        self.last_signal = None   # запоминаем последний отправленный сигнал

    async def on_candle(self, candle):
        close_price = candle[4]
        self.prices.append(close_price)
        if len(self.prices) < self.slow_period:
            return None

        df = pd.DataFrame({'close': self.prices})
        fast_sma = ta.sma(df['close'], length=self.fast_period).iloc[-1]
        slow_sma = ta.sma(df['close'], length=self.slow_period).iloc[-1]

        if fast_sma > slow_sma:
            signal = 'buy'
        elif fast_sma < slow_sma:
            signal = 'sell'
        else:
            signal = None

        # Отправляем сигнал, только если он изменился
        if signal and signal != self.last_signal:
            self.last_signal = signal
            return signal
        return None