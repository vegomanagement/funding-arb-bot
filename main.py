"""Главный цикл funding arb бота.

Запуск:
    python main.py

В paper режиме никакие реальные ордера не размещаются — всё в БД.
"""
import asyncio
import signal
import sys
from datetime import datetime
from loguru import logger

from config import load_config
from src.database.db import init_db, get_session
from src.database.models import Position, OpportunityLog
from src.exchanges.bybit_client import BybitClient
from src.exchanges.hyperliquid_client import HyperliquidClient
from src.scanner.opportunity_scanner import OpportunityScanner
from src.execution.paper_trader import PaperTrader
from src.monitoring.funding_monitor import FundingMonitor
from src.strategy.risk_manager import RiskManager
from src.notifications.telegram_notifier import TelegramNotifier


def setup_logging(level: str) -> None:
    logger.remove()
    logger.add(sys.stdout, level=level, format=(
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <7}</level> | <cyan>{name}</cyan> - {message}"
    ))
    logger.add("logs/bot.log", level=level, rotation="10 MB", retention="7 days")


class Bot:
    def __init__(self):
        self.cfg = load_config()
        setup_logging(self.cfg.log_level)
        init_db(self.cfg.db_path)

        self.bybit = BybitClient(
            api_key=self.cfg.bybit_api_key,
            api_secret=self.cfg.bybit_api_secret,
            testnet=self.cfg.bybit_testnet,
        )
        self.hl = HyperliquidClient(
            private_key=self.cfg.hl_private_key,
            address=self.cfg.hl_address,
            testnet=self.cfg.hl_testnet,
        )
        self.exchanges = {"bybit": self.bybit, "hyperliquid": self.hl}

        self.scanner = OpportunityScanner(
            exchanges=[self.bybit, self.hl],
            min_funding_diff_pct=self.cfg.min_funding_diff_pct,
            min_volume_24h_usd=self.cfg.min_volume_24h_usd,
        )
        self.notifier = TelegramNotifier(self.cfg.tg_token, self.cfg.tg_chat_id)
        self.trader = PaperTrader(self.exchanges, mode=self.cfg.mode)
        self.risk = RiskManager(
            capital_usd=self.cfg.capital_usd,
            max_positions=self.cfg.max_positions,
            max_position_pct=self.cfg.max_position_pct,
            stop_loss_pct=self.cfg.stop_loss_pct,
            daily_drawdown_limit_pct=self.cfg.daily_drawdown_limit_pct,
        )
        self.monitor = FundingMonitor(
            exchanges=self.exchanges,
            trader=self.trader,
            risk=self.risk,
            notifier=self.notifier,
            min_funding_diff_pct=self.cfg.min_funding_diff_pct,
            mode=self.cfg.mode,
        )
        self._stop = False

    async def _log_opportunity(self, op) -> None:
        with get_session() as s:
            s.add(OpportunityLog(
                symbol=op.symbol,
                short_exchange=op.short_exchange,
                short_rate_8h=op.short_rate_8h,
                long_exchange=op.long_exchange,
                long_rate_8h=op.long_rate_8h,
                profit_per_8h_pct=op.profit_per_8h_pct,
                basis_pct=op.basis_pct,
                min_volume_24h=op.min_volume_24h,
                taken=False,
            ))

    async def _process_opportunities(self, opportunities: list) -> None:
        """Решает по каждой возможности — открывать или нет."""
        for op in opportunities:
            await self._log_opportunity(op)

            ok, reason = self.risk.can_open(
                mode=self.cfg.mode,
                symbol=op.symbol,
                basis_pct=op.basis_pct,
            )
            if not ok:
                logger.debug(f"{op.symbol}: skip ({reason})")
                continue

            size = self.risk.position_size_usd()
            pos_id = await self.trader.open(op, size_usd=size)
            if pos_id:
                await self.notifier.opened(
                    symbol=op.symbol,
                    short_ex=op.short_exchange,
                    long_ex=op.long_exchange,
                    size_usd=size,
                    expected_profit_8h_pct=op.profit_per_8h_pct,
                )

    async def run(self) -> None:
        logger.info(f"=== Funding Arb Bot — mode: {self.cfg.mode} ===")
        logger.info(f"Capital: ${self.cfg.capital_usd:.0f}")
        logger.info(f"Max positions: {self.cfg.max_positions}")
        logger.info(f"Min funding diff: {self.cfg.min_funding_diff_pct}%")

        await self.notifier.send(
            f"🤖 Бот запущен (mode: <b>{self.cfg.mode}</b>)\n"
            f"Капитал: ${self.cfg.capital_usd:.0f}"
        )

        last_heartbeat = datetime.utcnow()
        while not self._stop:
            try:
                # 1. Мониторим открытые позиции (закрытия, funding выплаты)
                await self.monitor.check_all()

                # 2. Ищем новые возможности
                opportunities = await self.scanner.scan()
                if opportunities:
                    logger.info(f"Найдено {len(opportunities)} возможностей")
                    await self._process_opportunities(opportunities)

                # 3. Heartbeat в Telegram раз в час
                if (datetime.utcnow() - last_heartbeat).total_seconds() > 3600:
                    with get_session() as s:
                        open_count = s.query(Position).filter_by(
                            mode=self.cfg.mode, status="open"
                        ).count()
                    await self.notifier.send(f"💓 Heartbeat. Открыто позиций: {open_count}")
                    last_heartbeat = datetime.utcnow()

                await asyncio.sleep(self.cfg.scan_interval_sec)
            except Exception as e:
                logger.exception(f"Ошибка в главном цикле: {e}")
                await self.notifier.error(f"Ошибка в main loop: {e}")
                await asyncio.sleep(self.cfg.scan_interval_sec)

    async def shutdown(self) -> None:
        logger.info("Остановка бота...")
        self._stop = True
        await self.notifier.send("🛑 Бот остановлен")
        await self.bybit.close()
        await self.hl.close()
        await self.notifier.close()


async def main():
    bot = Bot()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(bot.shutdown()))

    try:
        await bot.run()
    finally:
        await bot.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
