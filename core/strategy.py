from abc import ABC, abstractmethod
from core.database import Trade
from sqlalchemy.ext.asyncio import AsyncSession

class BaseStrategy(ABC):
    def __init__(self, config):
        self.name = config.get('name', 'Unnamed')
        self.symbol = config['symbol']
        self.timeframe = config.get('timeframe', '1m')
        self.exchange = config['exchange']
        self.notifier = config.get('notifier')
        self.db_session_factory = config.get('db_session_factory')  # фабрика сессий
        self.position = None
        self.entry_price = None
        self.amount = config.get('amount', 0.001)  # для фьючерсов Bybit: 1 = 1 контракт (0.001 BTC)

    @abstractmethod
    async def on_candle(self, candle):
        pass

    async def execute_signal(self, signal):
        if signal == 'buy' and self.position != 'long':
            if self.position == 'short':
                await self.close_position()
            order = await self.exchange.create_market_order(self.symbol, 'buy', self.amount)
            if self._is_order_successful(order):
                self.position = 'long'
                self.entry_price = self._get_order_price(order)
                await self.notifier.notify(f"🟢 Открыт LONG {self.symbol} @ {self.entry_price}")
                await self._save_trade('buy', self.entry_price)
            else:
                print(f"❌ Не удалось открыть LONG: {order}")

        elif signal == 'sell' and self.position != 'short':
            if self.position == 'long':
                await self.close_position()
            order = await self.exchange.create_market_order(self.symbol, 'sell', self.amount)
            if self._is_order_successful(order):
                self.position = 'short'
                self.entry_price = self._get_order_price(order)
                await self.notifier.notify(f"🔴 Открыт SHORT {self.symbol} @ {self.entry_price}")
                await self._save_trade('sell', self.entry_price)
            else:
                print(f"❌ Не удалось открыть SHORT: {order}")

        elif signal == 'close' and self.position:
            await self.close_position()

    async def close_position(self):
        if self.position == 'long':
            side = 'sell'
        elif self.position == 'short':
            side = 'buy'
        else:
            return
        order = await self.exchange.create_market_order(self.symbol, side, self.amount)
        if self._is_order_successful(order):
            price = self._get_order_price(order)
            await self.notifier.notify(f"✅ Закрыта позиция {self.symbol} @ {price}")
            await self._save_trade(side, price)
            self.position = None
            self.entry_price = None
        else:
            print(f"❌ Не удалось закрыть позицию: {order}")

    def _is_order_successful(self, order):
        return (order is not None and 
                order.get('status') in ['closed', 'filled'] and 
                order.get('filled', 0) > 0)

    def _get_order_price(self, order):
        """Извлекает цену исполнения из ордера: сначала average, затем цена из cost/filled, затем price."""
        if order.get('average') is not None:
            return order['average']
        if order.get('cost') and order.get('filled'):
            return order['cost'] / order['filled']
        return order.get('price')  # fallback

    async def _save_trade(self, side, price):
        """Сохранить сделку в базу данных"""
        if self.db_session_factory:
            async with self.db_session_factory() as session:
                trade = Trade(
                    symbol=self.symbol,
                    side=side,
                    price=price,
                    amount=self.amount,
                    strategy=self.name
                )
                session.add(trade)
                await session.commit()