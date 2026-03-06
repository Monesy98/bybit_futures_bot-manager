import os
import asyncio
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from core.state import state

class TelegramNotifier:
    def __init__(self):
        self.token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.application = None
        self.queue = asyncio.Queue()
        self._sender_task = None
        self._updates_task = None

    async def start(self):
        if not self.token:
            print("Telegram bot token not provided, notifications disabled")
            return

        # Создаём Application
        self.application = Application.builder().token(self.token).build()

        # Регистрируем обработчики команд
        self.application.add_handler(CommandHandler("start_strategy", self.cmd_start_strategy))
        self.application.add_handler(CommandHandler("stop_all", self.cmd_stop_all))
        self.application.add_handler(CommandHandler("status", self.cmd_status))
        self.application.add_handler(CommandHandler("help", self.cmd_help))

        # Инициализируем и запускаем Application (без polling)
        await self.application.initialize()
        await self.application.start()

        # Запускаем polling в фоновом режиме через updater
        await self.application.updater.start_polling()

        # Задача для поддержания работы (ждёт отмены)
        self._updates_task = asyncio.create_task(self._keep_alive())
        # Задача для отправки сообщений
        self._sender_task = asyncio.create_task(self._sender())
        print("Telegram bot started")

    async def _keep_alive(self):
        """Держит приложение активным до отмены"""
        try:
            # Бесконечно ждём, пока не отменят задачу
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            # Корректно останавливаем приложение при отмене задачи
            await self.application.updater.stop()
            await self.application.stop()
            await self.application.shutdown()
            raise

    async def _sender(self):
        """Отправляет уведомления из очереди"""
        while True:
            message = await self.queue.get()
            try:
                await self.application.bot.send_message(chat_id=self.chat_id, text=message)
            except Exception as e:
                print(f"Failed to send telegram message: {e}")

    async def stop(self):
        """Останавливает Telegram бота"""
        if self._sender_task:
            self._sender_task.cancel()
            try:
                await self._sender_task
            except asyncio.CancelledError:
                pass
        if self._updates_task:
            self._updates_task.cancel()
            try:
                await self._updates_task
            except asyncio.CancelledError:
                pass

    async def notify(self, message):
        """Отправляет уведомление (ставит в очередь)"""
        if self.application:
            await self.queue.put(message)

    # ---- Обработчики команд ----

    async def _check_auth(self, update: Update) -> bool:
        if str(update.effective_chat.id) != str(self.chat_id):
            await update.message.reply_text("⛔ Неавторизованный чат")
            return False
        return True

    async def cmd_start_strategy(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._check_auth(update):
            return
        args = context.args
        if len(args) < 2:
            await update.message.reply_text(
                "❌ Использование: /start_strategy <название> <символ> [плечо]\n"
                "Доступные стратегии: ma_cross, chandelier_exit\n"
                "Пример: /start_strategy chandelier_exit BTC/USDT 3"
            )
            return
        strategy_name = args[0]
        symbol = args[1]
        leverage = int(args[2]) if len(args) > 2 else 3
        if not state.bot:
            await update.message.reply_text("❌ Бот не инициализирован")
            return
        await state.bot.start_strategy(strategy_name, symbol, leverage)
        await update.message.reply_text(f"✅ Запущена стратегия {strategy_name} для {symbol} с плечом {leverage}x")

    async def cmd_stop_all(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._check_auth(update):
            return
        if not state.bot:
            await update.message.reply_text("❌ Бот не инициализирован")
            return
        await state.bot.stop_all()
        await update.message.reply_text("⏹ Все стратегии остановлены")

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._check_auth(update):
            return
        if not state.bot:
            await update.message.reply_text("❌ Бот не инициализирован")
            return
        if not state.bot.strategies:
            await update.message.reply_text("📭 Нет активных стратегий")
            return
        lines = ["**Активные стратегии:**"]
        for s in state.bot.strategies:
            pos = f"{s.position.upper()} @ {s.entry_price}" if s.position else "нет позиции"
            lines.append(f"• {s.name} ({s.symbol}): {pos}")
        await update.message.reply_text("\n".join(lines))

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._check_auth(update):
            return
        help_text = (
            "🤖 **Доступные команды:**\n"
            "/start_strategy <имя> <символ> [плечо] – запустить стратегию\n"
            "/stop_all – остановить все стратегии\n"
            "/status – показать активные стратегии и позиции\n"
            "/help – это сообщение\n\n"
            "**Примеры:**\n"
            "/start_strategy ma_cross BTC/USDT 3\n"
            "/start_strategy chandelier_exit BTC/USDT 3"
        )
        await update.message.reply_text(help_text)