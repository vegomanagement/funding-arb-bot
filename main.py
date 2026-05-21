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
from src.database.db import init_db, get_session, cleanup_opportunities
from src.database.models import Position, OpportunityLog
from src.exchanges.bybit_client import BybitClient
from src.exchanges.hyperliquid_client import HyperliquidClient
from src.market_data.cache import MarketDataCache
from src.scanner.opportunity_scanner import OpportunityScanner
from src.execution.paper_trader import PaperTrader
from src.monitoring.funding_monitor import FundingMonitor
from src.strategy.risk_manager import RiskManager
from src.notifications.telegram_notifier import TelegramNotifier
from src.notifications.telegram_commands import TelegramCommands


def setup_logging(level: str) -> None:
    logger.remove()
    logger.add(sys.stdout, level=level, format=(
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <7}</level> | <cyan>{name}</cyan> - {message}"
    ))
    logger.add("/tmp/bot.log", level=level, rotation="10 MB", retention="7 days")


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

        self.cache = MarketDataCache([self.bybit, self.hl])
        self.scanner = OpportunityScanner(
            min_funding_diff_pct=self.cfg.min_funding_diff_pct,
            min_volume_24h_usd=self.cfg.min_volume_24h_usd,
        )
        self.notifier = TelegramNotifier(self.cfg.tg_token, self.cfg.tg_chat_id, tz=self.cfg.timezone)
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
        self.commands = TelegramCommands(self.cfg.tg_token, self.cfg.tg_chat_id)
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
                with get_session() as s:
                    pos = s.get(Position, pos_id)
                    entry_short = pos.entry_short_price
                    entry_long = pos.entry_long_price
                    fees = pos.fees_paid_usd
                short_info = self.cache.get_funding(op.short_exchange, op.symbol)
                long_info = self.cache.get_funding(op.long_exchange, op.symbol)
                await self.notifier.opened(
                    op=op,
                    size_usd=size,
                    entry_short_price=entry_short,
                    entry_long_price=entry_long,
                    fees_usd=fees or 0,
                    short_next_ts=short_info.next_funding_ts if short_info else 0,
                    long_next_ts=long_info.next_funding_ts if long_info else 0,
                )

    async def run(self) -> None:
        logger.info(f"=== Funding Arb Bot — mode: {self.cfg.mode} ===")
        logger.info(f"Capital: ${self.cfg.capital_usd:.0f}")
        logger.info(f"Max positions: {self.cfg.max_positions}")
        logger.info(f"Min funding diff: {self.cfg.min_funding_diff_pct}%")

        await self.notifier.started(self.cfg.mode, self.cfg.capital_usd)

        last_heartbeat = datetime.utcnow()
        last_cleanup = datetime.utcnow()
        while not self._stop:
            try:
                # 1. Один запрос к биржам на весь цикл
                await self.cache.refresh()

                # 2. Мониторим открытые позиции (закрытия, funding выплаты)
                await self.monitor.check_all(self.cache)

                # 3. Ищем новые возможности
                opportunities = self.scanner.scan(self.cache)
                if opportunities:
                    logger.info(f"Найдено {len(opportunities)} возможностей")
                    await self._process_opportunities(opportunities)

                # 3. Очистка старых opportunities раз в сутки
                if (datetime.utcnow() - last_cleanup).total_seconds() > 86400:
                    cleanup_opportunities(keep_days=7)
                    last_cleanup = datetime.utcnow()

                # 4. Heartbeat в Telegram раз в час
                if (datetime.utcnow() - last_heartbeat).total_seconds() > 3600:
                    await self._send_heartbeat()
                    last_heartbeat = datetime.utcnow()

                await asyncio.sleep(self.cfg.scan_interval_sec)
            except Exception as e:
                logger.exception(f"Ошибка в главном цикле: {e}")
                await self.notifier.error(f"Ошибка в main loop: {e}")
                await asyncio.sleep(self.cfg.scan_interval_sec)

    async def _send_heartbeat(self) -> None:
        from datetime import timezone as tz_module
        now_utc = datetime.now(tz_module.utc)
        with get_session() as s:
            open_positions = s.query(Position).filter_by(
                mode=self.cfg.mode, status="open"
            ).all()
            today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
            closed_today = s.query(Position).filter(
                Position.mode == self.cfg.mode,
                Position.status == "closed",
                Position.closed_at >= today_start,
            ).all()
            day_pnl = sum(p.total_pnl_usd or 0 for p in closed_today)
            pos_list = []
            for p in open_positions:
                opened = p.opened_at.replace(tzinfo=tz_module.utc)
                dur_min = int((now_utc - opened).total_seconds() // 60)
                h, m = divmod(dur_min, 60)
                dur_str = f"{h}ч {m}м" if h else f"{m}м"
                pos_list.append({
                    "symbol": p.symbol,
                    "pnl": p.total_pnl_usd or 0,
                    "duration": dur_str,
                })
        await self.notifier.heartbeat(pos_list, day_pnl)

    async def shutdown(self) -> None:
        logger.info("Остановка бота...")
        self._stop = True
        await self.notifier.send("🛑 Бот остановлен")
        await self.bybit.close()
        await self.hl.close()
        await self.notifier.close()
        await self.commands.close()


async def main():
    bot = Bot()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(bot.shutdown()))

    try:
        # Запускаем основной бот и Telegram команды параллельно
        await asyncio.gather(
            bot.run(),
            bot.commands.run(mode=bot.cfg.mode),
        )
    finally:
        await bot.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
