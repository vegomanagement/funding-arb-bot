"""Мониторинг открытых позиций.

Каждую итерацию:
  - пересчитывает unrealized PnL по mark price из кеша
  - проверяет predicted funding → закрывает если ставка перевернулась
  - проверяет basis → закрывает если расширился
  - проверяет stop-loss
  - симулирует выплату funding когда наступило время (состояние хранится в БД)
"""
from datetime import datetime
from loguru import logger

from src.database.db import get_session
from src.database.models import Position, FundingEvent
from src.exchanges.base import ExchangeBase
from src.execution.base import TraderBase
from src.market_data.cache import MarketDataCache
from src.strategy.risk_manager import RiskManager
from src.notifications.telegram_notifier import TelegramNotifier


class FundingMonitor:
    def __init__(
        self,
        exchanges: dict[str, ExchangeBase],
        trader: TraderBase,
        risk: RiskManager,
        notifier: TelegramNotifier,
        min_funding_diff_pct: float,
        mode: str,
    ):
        self.exchanges = exchanges
        self.trader = trader
        self.risk = risk
        self.notifier = notifier
        self.min_diff = min_funding_diff_pct
        self.mode = mode

    async def check_all(self, cache: MarketDataCache) -> None:
        """Проверить все открытые позиции используя данные из кеша."""
        with get_session() as s:
            position_ids = [
                p.id for p in s.query(Position).filter_by(mode=self.mode, status="open").all()
            ]
        for pid in position_ids:
            await self._check_one(pid, cache)

    async def _check_one(self, position_id: int, cache: MarketDataCache) -> None:
        with get_session() as s:
            pos = s.get(Position, position_id)
            if not pos or pos.status != "open":
                return
            symbol = pos.symbol
            short_ex_name = pos.short_exchange
            long_ex_name = pos.long_exchange
            size_coin = pos.size_coin
            entry_short = pos.entry_short_price
            entry_long = pos.entry_long_price

        short_info = cache.get_funding(short_ex_name, symbol)
        long_info = cache.get_funding(long_ex_name, symbol)
        if not short_info or not long_info:
            logger.warning(f"{symbol}: нет данных в кеше для {short_ex_name} или {long_ex_name}")
            return

        # 1. Симулируем выплату funding
        await self._maybe_pay_funding(position_id, short_ex_name, short_info, size_coin, is_short=True)
        await self._maybe_pay_funding(position_id, long_ex_name, long_info, size_coin, is_short=False)

        # 2. Обновляем unrealized PnL по mark price
        unrealized_short = (entry_short - short_info.mark_price) * size_coin
        unrealized_long = (long_info.mark_price - entry_long) * size_coin
        unrealized = unrealized_short + unrealized_long

        with get_session() as s:
            pos = s.get(Position, position_id)
            if not pos:
                return
            pos.price_pnl_usd = unrealized
            pos.total_pnl_usd = (
                (pos.funding_received_usd or 0) + unrealized - (pos.fees_paid_usd or 0)
            )

        # 3. Stop-loss
        with get_session() as s:
            pos = s.get(Position, position_id)
            if not pos:
                return
            if self.risk.should_stop_loss(pos):
                logger.warning(f"{symbol}: stop-loss сработал PnL=${pos.total_pnl_usd:.4f}")
                await self._close(position_id, "stop_loss")
                return

        # 4. Funding перевернулся
        current_diff = short_info.current_rate_8h - long_info.current_rate_8h
        if current_diff < 0:
            logger.info(f"{symbol}: funding перевернулся (diff={current_diff:.4f}%) → CLOSE")
            await self._close(position_id, "funding_flipped")
            return

        # 5. Funding diff упал ниже половины порога
        if current_diff < self.min_diff * 0.5:
            logger.info(f"{symbol}: funding diff упал до {current_diff:.4f}% → CLOSE")
            await self._close(position_id, "funding_low")
            return

        # 6. Basis расширился
        avg = (short_info.mark_price + long_info.mark_price) / 2
        basis_pct = abs(short_info.mark_price - long_info.mark_price) / avg * 100 if avg else 0
        if basis_pct > self.risk.max_basis_pct:
            logger.warning(f"{symbol}: basis {basis_pct:.2f}% → CLOSE")
            await self._close(position_id, "basis_wide")
            return

    async def _maybe_pay_funding(
        self,
        position_id: int,
        exchange_name: str,
        info,
        size_coin: float,
        is_short: bool,
    ) -> None:
        """Начислить funding если прошёл хотя бы один период с последней выплаты.

        Время последней выплаты берётся из последнего FundingEvent в БД
        (или из opened_at позиции если событий ещё не было).
        Это позволяет корректно восстанавливать состояние после перезапуска.
        """
        period_hours = self.exchanges[exchange_name].funding_interval_hours
        now = datetime.utcnow()

        with get_session() as s:
            last_event = (
                s.query(FundingEvent)
                .filter_by(position_id=position_id, exchange=exchange_name)
                .order_by(FundingEvent.paid_at.desc())
                .first()
            )
            if last_event is None:
                pos = s.get(Position, position_id)
                last_paid = pos.opened_at if pos else now
            else:
                last_paid = last_event.paid_at

        elapsed_hours = (now - last_paid).total_seconds() / 3600
        if elapsed_hours < period_hours:
            return

        periods_passed = int(elapsed_hours // period_hours)
        rate_per_period_pct = info.current_rate_8h * (period_hours / 8)

        sign = 1 if is_short else -1
        amount = sign * (size_coin * info.mark_price * rate_per_period_pct / 100) * periods_passed

        self.trader.record_funding(
            position_id=position_id,
            exchange=exchange_name,
            amount_usd=amount,
            rate_pct=rate_per_period_pct,
            period_hours=period_hours,
        )
        logger.info(
            f"{info.symbol} funding на {exchange_name}: "
            f"{rate_per_period_pct:+.4f}%/{period_hours}h × {periods_passed} = ${amount:+.4f}"
        )
        with get_session() as s:
            pos = s.get(Position, position_id)
            total_funding = pos.funding_received_usd or 0 if pos else 0
        await self.notifier.funding_paid(
            symbol=info.symbol,
            exchange=exchange_name,
            amount_usd=amount,
            total_funding_usd=total_funding,
            next_ts=info.next_funding_ts,
            interval_hours=period_hours,
        )

    async def _close(self, position_id: int, reason: str) -> None:
        pnl = await self.trader.close(position_id, reason)
        if pnl is None:
            return
        with get_session() as s:
            pos = s.get(Position, position_id)
            if pos:
                await self.notifier.closed(
                    symbol=pos.symbol,
                    reason=reason,
                    total_pnl=pos.total_pnl_usd or 0,
                    funding_received=pos.funding_received_usd or 0,
                    price_pnl=pos.price_pnl_usd or 0,
                    fees=pos.fees_paid_usd or 0,
                    opened_at=pos.opened_at,
                )
