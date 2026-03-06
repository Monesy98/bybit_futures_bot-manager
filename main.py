import os
import asyncio
from dotenv import load_dotenv
import uvicorn
from fastapi import FastAPI
from contextlib import asynccontextmanager

from core.exchange import BybitExchange
from core.notifier import TelegramNotifier
from core.database import init_db
from core.state import state   # новый импорт
from core.database import AsyncSessionLocal

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

    async def start_strategy(self, strategy_name: str, symbol: str, timeframe: str = '1m', amount: float = 1.0, leverage: int = 3, margin_mode: str = 'isolated', process_updates: bool = False, fast_period: int = 9, slow_period: int = 21, length: int = 22, multiplier: float = 3.0, use_close: bool = True, display_name: str = None):
        # Определяем имя стратегии
        if display_name:
            name = display_name
        else:
            symbol_clean = symbol.replace('/', '_')
            name = f"{strategy_name}_{symbol_clean}"
        
        # Проверяем, не запущена ли уже стратегия с таким именем
        for s in self.strategies:
            if s.name == name:
                await self.notifier.notify(f"⚠️ Стратегия с именем {name} уже запущена")
                return

        await self.exchange.set_leverage_and_margin(symbol, leverage, margin_mode=margin_mode)

        config = {
            'name': name,
            'symbol': symbol,
            'timeframe': timeframe,
            'exchange': self.exchange,
            'notifier': self.notifier,
            'amount': amount,
            'leverage': leverage,
            'margin_mode': margin_mode,
            'process_updates': process_updates,
            'db_session_factory': AsyncSessionLocal
        }

        if strategy_name == "ma_cross":
            config.update({'fast_period': fast_period, 'slow_period': slow_period})
            strategy = MovingAverageCross(config)
        elif strategy_name == "chandelier_exit":
            config.update({'length': length, 'multiplier': multiplier, 'use_close': use_close})
            strategy = ChandelierExitStrategy(config)
        else:
            print(f"❌ Неизвестная стратегия: {strategy_name}")
            return

        await self._sync_with_existing_positions(strategy)

        self.strategies.append(strategy)
        task = asyncio.create_task(self.run_strategy(strategy))
        self.tasks.append(task)
        await self.notifier.notify(f"🚀 Запущена стратегия {strategy_name} для {symbol} с плечом {leverage}x")

    async def run_strategy(self, strategy):
        last_ts = None
        while self.is_running:
            try:
                async for candle in self.exchange.watch_ohlcv(strategy.symbol, strategy.timeframe):
                    # Если process_updates = False, пропускаем обновления незакрытой свечи
                    if not getattr(strategy, 'process_updates', False) and last_ts is not None and candle[0] == last_ts:
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
        
    async def stop_strategy(self, name: str, close_position=True):
        """Останавливает конкретную стратегию по имени"""
        for i, s in enumerate(self.strategies):
            if s.name == name:
                # Сначала отменяем задачу стратегии
                if i < len(self.tasks):
                    self.tasks[i].cancel()
                    try:
                        await self.tasks[i]
                    except asyncio.CancelledError:
                        pass
                    del self.tasks[i]
                
                # Затем закрываем позицию (если нужно)
                if close_position and s.position:
                    await s.close_position()
                
                del self.strategies[i]
                await self.notifier.notify(f"⏹ Стратегия {name} остановлена")
                return True
        return False        

    async def stop_all(self, close_positions=True):
        """
        Останавливает все стратегии.
        Если close_positions=True, после остановки закрывает все открытые позиции.
        """
        self.is_running = False
        # Отменяем все задачи стратегий
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        
        # Закрываем позиции (если требуется)
        if close_positions:
            for strategy in self.strategies:
                if strategy.position:
                    await strategy.close_position()
        
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