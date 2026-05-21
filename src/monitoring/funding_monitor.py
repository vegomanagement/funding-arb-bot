"""Мониторинг открытых позиций.

Каждую итерацию:
  - обновляет mark price → пересчитывает unrealized PnL
  - проверяет predicted funding → закрывает если ставка перевернулась
  - проверяет basis → закрывает если расширился
  - проверяет stop-loss
  - симулирует выплату funding когда наступило время
"""
from datetime import datetime
from loguru import logger

from src.database.db import get_session
from src.database.models import Position
from src.exchanges.base import ExchangeBase
from src.execution.paper_trader import PaperTrader
from src.strategy.risk_manager import RiskManager
from src.notifications.telegram_notifier import TelegramNotifier


class FundingMonitor:
    def __init__(
        self,
        exchanges: dict[str, ExchangeBase],
        trader: PaperTrader,
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
        # Хранит timestamp последнего "получения" funding для каждой позиции и биржи
        # ключ: (position_id, exchange_name) → ts последнего платежа
        self._last_funding_ts: dict[tuple[int, str], int] = {}

    async def check_all(self) -> None:
        """Проверить все открытые позиции."""
        with get_session() as s:
            positions = s.query(Position).filter_by(
                mode=self.mode, status="open"
            ).all()
            position_ids = [p.id for p in positions]

        for pid in position_ids:
            await self._check_one(pid)

    async def _check_one(self, position_id: int) -> None:
        with get_session() as s:
            pos = s.get(Position, position_id)
            if not pos or pos.status != "open":
                return
            symbol = pos.symbol
            short_ex_name = pos.short_exchange
            long_ex_name = pos.long_exchange
            size_usd = pos.size_usd
            size_coin = pos.size_coin
            entry_short = pos.entry_short_price
            entry_long = pos.entry_long_price

        short_ex = self.exchanges[short_ex_name]
        long_ex = self.exchanges[long_ex_name]

        # 1. Получаем актуальные funding rates
        short_info = await short_ex.get_funding_rate(symbol)
        long_info = await long_ex.get_funding_rate(symbol)
        if not short_info or not long_info:
            logger.warning(f"{symbol}: funding rate недоступен")
            return

        # 2. Симулируем выплату funding
        await self._maybe_pay_funding(position_id, short_ex, short_info, size_coin, is_short=True)
        await self._maybe_pay_funding(position_id, long_ex, long_info, size_coin, is_short=False)

        # 3. Обновляем mark price PnL
        unrealized_short = (entry_short - short_info.mark_price) * size_coin
        unrealized_long = (long_info.mark_price - entry_long) * size_coin
        unrealized = unrealized_short + unrealized_long

        with get_session() as s:
            pos = s.get(Position, position_id)
            if not pos:
                return
            # Текущий PnL = realized funding + текущий price pnl - fees
            pos.price_pnl_usd = unrealized
            pos.total_pnl_usd = (
                (pos.funding_received_usd or 0) + unrealized - (pos.fees_paid_usd or 0)
            )
            current_pnl = pos.total_pnl_usd

        # 4. Stop-loss
        with get_session() as s:
            pos = s.get(Position, position_id)
            if self.risk.should_stop_loss(pos):
                logger.warning(f"{symbol}: stop-loss сработал PnL=${current_pnl:.4f}")
                await self._close(position_id, "stop_loss")
                return

        # 5. Funding перевернулся → закрываем
        current_diff = short_info.current_rate_8h - long_info.current_rate_8h
        if current_diff < 0:
            logger.info(f"{symbol}: funding перевернулся (diff={current_diff:.4f}%) → CLOSE")
            await self._close(position_id, "funding_flipped")
            return

        # 6. Funding diff упал ниже минимума
        if current_diff < self.min_diff * 0.5:
            logger.info(f"{symbol}: funding diff упал до {current_diff:.4f}% → CLOSE")
            await self._close(position_id, "funding_low")
            return

        # 7. Basis расширился
        avg = (short_info.mark_price + long_info.mark_price) / 2
        basis_pct = abs(short_info.mark_price - long_info.mark_price) / avg * 100 if avg else 0
        if basis_pct > self.risk.max_basis_pct:
            logger.warning(f"{symbol}: basis {basis_pct:.2f}% → CLOSE")
            await self._close(position_id, "basis_wide")
            return

    async def _maybe_pay_funding(
        self, position_id: int, exchange: ExchangeBase, info,
        size_coin: float, is_short: bool,
    ) -> None:
        """Симулирует получение/выплату funding когда наступило время.

        В paper mode симулируем по графику: каждые funding_interval_hours
        начисляется сумма = size_coin * mark * rate / 100
        """
        period_hours = exchange.funding_interval_hours
        now_ts = int(datetime.utcnow().timestamp())
        key = (position_id, exchange.name)
        last_ts = self._last_funding_ts.get(key)

        if last_ts is None:
            # Первая встреча — отметим текущий момент как точку отсчёта
            self._last_funding_ts[key] = now_ts
            return

        elapsed_hours = (now_ts - last_ts) / 3600
        if elapsed_hours < period_hours:
            return

        # Считаем что прошёл хотя бы один период
        periods_passed = int(elapsed_hours // period_hours)
        # Конвертируем ставку 8h в ставку периода биржи
        rate_per_period_pct = info.current_rate_8h * (period_hours / 8)

        # Шортист получает положительный funding если ставка > 0
        # Лонгист платит если ставка > 0
        sign = 1 if is_short else -1
        amount_per_period = sign * (size_coin * info.mark_price * rate_per_period_pct / 100)
        amount = amount_per_period * periods_passed

        self.trader.record_funding(
            position_id=position_id,
            exchange=exchange.name,
            amount_usd=amount,
            rate_pct=rate_per_period_pct,
            period_hours=period_hours,
        )
        self._last_funding_ts[key] = now_ts

        await self.notifier.funding_paid(info.symbol, exchange.name, amount)
        logger.info(
            f"{info.symbol} funding на {exchange.name}: "
            f"{rate_per_period_pct:+.4f}%/{period_hours}h × {periods_passed} = ${amount:+.4f}"
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
                    fees=pos.fees_paid_usd or 0,
                )
