import os
import ccxt.pro as ccxtpro
import asyncio
import time
from datetime import datetime

class BybitExchange:
    def __init__(self):
        self.api_key = os.getenv("BYBIT_API_KEY")
        self.api_secret = os.getenv("BYBIT_API_SECRET")
        self.exchange = ccxtpro.bybit({
            'apiKey': self.api_key,
            'secret': self.api_secret,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'linear',
            },
        })
        self.is_running = False
        self._markets_loaded = False

    def _normalize_symbol(self, symbol: str) -> str:
        if ':USDT' in symbol:
            return symbol
        if self.exchange.options.get('defaultType') == 'linear' and symbol.endswith('/USDT'):
            base = symbol.split('/')[0]
            return f"{base}/USDT:USDT"
        return symbol

    async def _ensure_markets_loaded(self):
        if not self._markets_loaded:
            await self.exchange.load_markets()
            self._markets_loaded = True

    async def set_leverage_and_margin(self, symbol: str, leverage: int, margin_mode: str = 'isolated'):
        try:
            await self._ensure_markets_loaded()
            normalized_symbol = self._normalize_symbol(symbol)
            market = self.exchange.market(normalized_symbol)
            unified_symbol = market['symbol']
            await self.exchange.set_margin_mode(margin_mode, unified_symbol)
            await self.exchange.set_leverage(leverage, unified_symbol)
            print(f"Leverage set to {leverage}x, margin mode {margin_mode} for {symbol} (using {unified_symbol})")
        except Exception as e:
            error_str = str(e)
            if "leverage not modified" in error_str or "110043" in error_str:
                print(f"ℹ️ Leverage already set to {leverage}x for {symbol}")
            else:
                print(f"❌ Failed to set leverage/margin for {symbol}: {e}")

    async def watch_ohlcv(self, symbol, timeframe='1m'):
        normalized_symbol = self._normalize_symbol(symbol)
        while self.is_running:
            try:
                candles = await self.exchange.watch_ohlcv(normalized_symbol, timeframe)
                # print(f"[{datetime.now()}] New candle received")  # отладка
                yield candles[-1]
            except Exception as e:
                print(f"⚠️ WebSocket error: {e}, reconnecting in 5s...")
                await asyncio.sleep(5)
                # Опционально: пересоздать подключение
                await self.reconnect()
                
    async def reconnect(self):
        print("🔄 Recreating exchange connection...")
        await self.exchange.close()
        self.exchange = ccxtpro.bybit({
            'apiKey': self.api_key,
            'secret': self.api_secret,
            'enableRateLimit': True,
            'options': {'defaultType': 'linear'},
        })
        self._markets_loaded = False                

    async def create_market_order(self, symbol, side, amount, wait_fill=True, timeout=10):
        normalized_symbol = self._normalize_symbol(symbol)
        try:
            order = await self.exchange.create_order(normalized_symbol, 'market', side, amount)
            print(f"📦 Order created: {side} {amount} {symbol} id={order['id']}")
            if wait_fill:
                filled_order = await self._wait_for_order_fill(order['id'], normalized_symbol, timeout)
                if filled_order:
                    # print(f"✅ Order filled: {filled_order}")
                    return filled_order
                else:
                    print(f"⚠️ Order not filled within timeout")
                    return None
            return order
        except Exception as e:
            print(f"❌ Order error: {e}")
            return None

    async def _wait_for_order_fill(self, order_id, symbol, timeout=10):
        """Ожидает исполнения ордера и возвращает обновлённый ордер с параметром acknowledged=True"""
        start = time.time()
        while time.time() - start < timeout:
            try:
                # Критично: добавляем params={'acknowledged': True} для получения полной информации об ордере
                order = await self.exchange.fetch_order(order_id, symbol, params={'acknowledged': True})
                if order['status'] in ['closed', 'filled'] and order.get('filled', 0) > 0:
                    return order
                await asyncio.sleep(0.5)
            except Exception as e:
                print(f"Error fetching order: {e}")
                await asyncio.sleep(1)
        return None

    async def fetch_positions(self):
        try:
            positions = await self.exchange.fetch_positions()
            return positions
        except Exception as e:
            print(f"Fetch positions error: {e}")
            return []

    async def close(self):
        await self.exchange.close()