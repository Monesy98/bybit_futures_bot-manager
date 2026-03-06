import os
import asyncio
from dotenv import load_dotenv
import uvicorn
from fastapi import FastAPI
from contextlib import asynccontextmanager

from core.exchange import BybitExchange
from core.notifier import TelegramNotifier
from core.database import init_db
from core.state import state
from core.database import AsyncSessionLocal

# Импорты стратегий
from strategies.ma_cross import MovingAverageCross
from strategies.chandelier_exit import ChandelierExitStrategy

load_dotenv()

class TradingBot:
    def __init__(self):
        self.exchange = None
        self.notifier = TelegramNotifier()
        self.strategies = []
        self.tasks = []
        self.is_running = False

    async def initialize(self):
        await init_db()
        await self.notifier.start()
        self.exchange = BybitExchange()
        self.exchange.is_running = True
        print("Bot initialized")

    async def _sync_with_existing_positions(self, strategy):
        """
        Синхронизирует состояние стратегии с открытыми позициями на бирже.
        Вызывается сразу после создания стратегии.
        """
        try:
            # Получаем все открытые позиции
            positions = await self.exchange.fetch_positions()
            
            # Нормализуем символ стратегии для сравнения
            normalized_symbol = self.exchange._normalize_symbol(strategy.symbol)
            
            for pos in positions:
                # Проверяем, совпадает ли символ и есть ли открытая позиция
                if pos['symbol'] == normalized_symbol and float(pos.get('contracts', 0)) > 0:
                    # Определяем сторону позиции
                    if pos['side'] == 'buy':
                        strategy.position = 'long'
                    elif pos['side'] == 'sell':
                        strategy.position = 'short'
                    else:
                        continue
                    
                    # Сохраняем цену входа и объём
                    strategy.entry_price = float(pos.get('entryPrice', 0))
                    strategy.amount = float(pos.get('contracts', 0))
                    
                    print(f"🔄 Восстановлена позиция: {strategy.position} {strategy.amount} @ {strategy.entry_price} для {strategy.symbol}")
                    await self.notifier.notify(f"🔄 Восстановлена позиция {strategy.position} для {strategy.symbol}")
                    
                    # Можно также сохранить информацию о позиции в БД как отдельное событие
                    break
        except Exception as e:
            print(f"❌ Ошибка при синхронизации позиций: {e}")

    async def start_strategy(self, strategy_name: str, symbol: str, leverage: int = 3):
        # Устанавливаем плечо и режим маржи
        await self.exchange.set_leverage_and_margin(symbol, leverage, margin_mode='isolated')

        # Базовый конфиг для всех стратегий
        config = {
            'name': f"{strategy_name}_{symbol}",
            'symbol': symbol,
            'timeframe': '5m',
            'exchange': self.exchange,
            'notifier': self.notifier,
            'amount': 0.001,  # количество контрактов
            'leverage': leverage,
            'db_session_factory': AsyncSessionLocal
        }

        if strategy_name == "ma_cross":
            config.update({'fast_period': 9, 'slow_period': 21})
            strategy = MovingAverageCross(config)
        elif strategy_name == "chandelier_exit":
            config.update({
                'length': 1,
                'multiplier': 3.0,
                'use_close': True
            })
            strategy = ChandelierExitStrategy(config)
        else:
            print(f"❌ Неизвестная стратегия: {strategy_name}")
            return

        # ВАЖНО: синхронизируем состояние с биржей перед запуском
        await self._sync_with_existing_positions(strategy)

        self.strategies.append(strategy)
        task = asyncio.create_task(self.run_strategy(strategy))
        self.tasks.append(task)
        await self.notifier.notify(f"🚀 Запущена стратегия {strategy_name} для {symbol} с плечом {leverage}x")

    async def run_strategy(self, strategy):
        """Запускает стратегию, обрабатывая только закрытые свечи (уникальный timestamp)"""
        last_ts = None
        while self.is_running:
            try:
                async for candle in self.exchange.watch_ohlcv(strategy.symbol, strategy.timeframe):
                    # Пропускаем обновления незакрытой свечи (с тем же timestamp)
                    if last_ts is not None and candle[0] == last_ts:
                        continue
                    last_ts = candle[0]
                    signal = await strategy.on_candle(candle)
                    if signal:
                        await strategy.execute_signal(signal)
            except Exception as e:
                print(f"⚠️ Стратегия {strategy.name} упала с ошибкой: {e}, перезапуск через 10 секунд...")
                await asyncio.sleep(10)

    async def close_all_positions(self):
        """Закрывает все открытые позиции по всем активным стратегиям"""
        if not self.strategies:
            return
        print("🔒 Закрытие всех позиций...")
        for strategy in self.strategies:
            if strategy.position:
                await strategy.close_position()
        await self.notifier.notify("🔒 Все позиции закрыты")

    async def stop_all(self, close_positions=True):
        """
        Останавливает все стратегии.
        Если close_positions=True, предварительно закрывает все открытые позиции.
        """
        if close_positions:
            await self.close_all_positions()
        self.is_running = False
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.strategies.clear()
        self.tasks.clear()
        await self.notifier.notify("⏹ Все стратегии остановлены")

    async def shutdown(self):
        """Корректное завершение работы (вызывается при остановке сервера)"""
        print("🛑 Завершение работы...")
        await self.stop_all(close_positions=True)   # закрываем позиции
        await self.exchange.close()
        await self.notifier.stop()

bot_instance = TradingBot()
state.bot = bot_instance        # сохраняем бота в state

# Импортируем веб-приложение ПОСЛЕ присвоения
from web.app import app as web_app

@asynccontextmanager
async def lifespan(app: FastAPI):
    await bot_instance.initialize()
    bot_instance.is_running = True
    yield
    await bot_instance.shutdown()

web_app.router.lifespan_context = lifespan

async def main():
    config = uvicorn.Config(web_app, host="0.0.0.0", port=8000, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()

if __name__ == "__main__":
    asyncio.run(main())