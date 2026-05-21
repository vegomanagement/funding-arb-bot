"""Funding Squeeze Bot.

Каждые 10 секунд:
  1. Сканирует Bybit на монеты с |funding| > порога и скоро выплата
  2. Входит в позицию за 45 секунд до выплаты
  3. Мониторит SL/TP каждые 3 секунды
  4. Закрывает позицию по TP / SL / timeout

Запуск:
    cp .env.example .env
    python main.py
"""
import asyncio
import signal
import sys
from loguru import logger

from config import load_config
from src.database.db import init_db
from src.exchanges.bybit_client import BybitClient
from src.scanner.squeeze_scanner import SqueezeScanner
from src.execution.squeeze_trader import SqueezeTrader
from src.notifications.telegram_notifier import TelegramNotifier
from src.notifications.telegram_commands import TelegramCommands


def setup_logging(level: str):
    logger.remove()
    logger.add(sys.stdout, level=level, format=(
        "<green>{time:HH:mm:ss}</green> | "
        "<level>{level: <7}</level> | {message}"
    ))
    logger.add("logs/squeeze.log", level=level, rotation="10 MB", retention="7 days")


class SqueezeBot:
    def __init__(self):
        self.cfg = load_config()
        setup_logging(self.cfg.log_level)
        init_db(self.cfg.db_path)

        self.client = BybitClient(
            api_key=self.cfg.bybit_api_key,
            api_secret=self.cfg.bybit_api_secret,
            testnet=self.cfg.bybit_testnet,
        )
        self.notifier = TelegramNotifier(self.cfg.tg_token, self.cfg.tg_chat_id)
        self.scanner = SqueezeScanner(
            client=self.client,
            min_funding_abs_pct=self.cfg.min_funding_abs_pct,
            entry_before_sec=self.cfg.entry_before_sec,
        )
        self.trader = SqueezeTrader(
            client=self.client,
            notifier=self.notifier,
            mode=self.cfg.mode,
            stop_loss_pct=self.cfg.stop_loss_pct,
            take_profit_pct=self.cfg.take_profit_pct,
            max_age_sec=self.cfg.max_position_age_sec,
        )
        self.commands = TelegramCommands(self.cfg.tg_token, self.cfg.tg_chat_id)
        self._stop = False

    async def run(self):
        logger.info(f"=== Squeeze Bot — mode: {self.cfg.mode} ===")
        logger.info(
            f"Min funding: {self.cfg.min_funding_abs_pct}% | "
            f"Entry before: {self.cfg.entry_before_sec}s | "
            f"SL: {self.cfg.stop_loss_pct}% TP: {self.cfg.take_profit_pct}%"
        )
        await self.notifier.send(
            f"⚡️ Squeeze Bot запущен (mode: <b>{self.cfg.mode}</b>)\n"
            f"Порог funding: {self.cfg.min_funding_abs_pct}%\n"
            f"SL: {self.cfg.stop_loss_pct}% | TP: {self.cfg.take_profit_pct}%"
        )

        while not self._stop:
            try:
                signals = await self.scanner.scan()

                for sig in signals:
                    # Пропускаем если уже в позиции по этой монете
                    if self.trader.has_position(sig.symbol):
                        continue

                    # Пропускаем если достигнут лимит позиций
                    from src.database.db import get_session
                    from src.database.models import SqueezeTrade
                    with get_session() as s:
                        open_count = s.query(SqueezeTrade).filter_by(
                            mode=self.cfg.mode
                        ).filter(SqueezeTrade.closed_at.is_(None)).count()

                    if open_count >= self.cfg.max_positions:
                        break

                    logger.info(
                        f"СИГНАЛ {sig.symbol} {'ЛОНГ' if sig.side=='Buy' else 'ШОРТ'} "
                        f"funding={sig.expected_funding_pct:.4f}% "
                        f"через {sig.seconds_left}с"
                    )
                    await self.notifier.signal_found(
                        sig.symbol, sig.side,
                        sig.expected_funding_pct,
                        sig.seconds_left,
                        sig.mark_price,
                    )
                    await self.trader.open(sig, self.cfg.position_size_usd)

            except Exception as e:
                logger.exception(f"Ошибка в main loop: {e}")
                await self.notifier.error(str(e))

            await asyncio.sleep(self.cfg.scan_interval_sec)

    async def shutdown(self):
        logger.info("Остановка...")
        self._stop = True
        for task in self.trader._active.values():
            task.cancel()
        await self.notifier.send("🛑 Squeeze Bot остановлен")
        await self.client.close()
        await self.notifier.close()
        await self.commands.close()


async def main():
    bot = SqueezeBot()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(bot.shutdown()))
    try:
        await asyncio.gather(
            bot.run(),
            bot.commands.run(mode=bot.cfg.mode),
        )
    finally:
        await bot.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
