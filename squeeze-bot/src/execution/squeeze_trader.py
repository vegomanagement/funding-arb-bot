"""Управление позицией для funding squeeze.

Логика после входа:
  1. Каждые 3 секунды проверяем mark price
  2. Если цена достигла TP → закрываем (tp)
  3. Если цена достигла SL → закрываем (sl)
  4. Если прошло MAX_POSITION_AGE_SEC секунд → закрываем (timeout)
  5. Если funding уже выплачен (прошло время next_funding_ts) → закрываем (funding_paid)

В paper режиме симулируем всё на mark price без реальных ордеров.
"""
import asyncio
from datetime import datetime
from loguru import logger

from src.exchanges.bybit_client import BybitClient
from src.database.db import get_session
from src.database.models import SqueezeTrade
from src.notifications.telegram_notifier import TelegramNotifier
from src.scanner.squeeze_scanner import SqueezeSignal


class SqueezeTrader:
    def __init__(
        self,
        client: BybitClient,
        notifier: TelegramNotifier,
        mode: str,
        stop_loss_pct: float,
        take_profit_pct: float,
        max_age_sec: int,
    ):
        self.client = client
        self.notifier = notifier
        self.mode = mode
        self.sl_pct = stop_loss_pct
        self.tp_pct = take_profit_pct
        self.max_age = max_age_sec
        # Активные позиции: {trade_id: {signal, next_funding_ts, task}}
        self._active: dict[int, asyncio.Task] = {}

    def has_position(self, symbol: str) -> bool:
        with get_session() as s:
            exists = s.query(SqueezeTrade).filter_by(
                mode=self.mode, symbol=symbol
            ).filter(SqueezeTrade.closed_at.is_(None)).first()
            return exists is not None

    async def open(self, signal: SqueezeSignal, size_usd: float) -> int | None:
        """Открыть позицию. Возвращает trade_id."""
        entry = signal.mark_price
        fee = size_usd * self.client.taker_fee

        with get_session() as s:
            trade = SqueezeTrade(
                mode=self.mode,
                symbol=signal.symbol,
                side=signal.side,
                entry_price=entry,
                size_usd=size_usd,
                entry_funding_pct=signal.expected_funding_pct,
                fees_usd=fee,
            )
            s.add(trade)
            s.flush()
            trade_id = trade.id

        logger.info(
            f"[{self.mode}] OPEN {signal.symbol} {signal.side} "
            f"@{entry} size=${size_usd:.2f} funding={signal.expected_funding_pct:.4f}% "
            f"SL={self.sl_pct}% TP={self.tp_pct}%"
        )
        await self.notifier.opened(
            signal.symbol, signal.side, entry,
            size_usd, signal.expected_funding_pct,
        )

        # Запускаем мониторинг в фоне
        task = asyncio.create_task(
            self._monitor(trade_id, signal)
        )
        self._active[trade_id] = task
        return trade_id

    async def _monitor(self, trade_id: int, signal: SqueezeSignal):
        """Мониторит позицию каждые 3 секунды до закрытия."""
        with get_session() as s:
            trade = s.get(SqueezeTrade, trade_id)
            entry = trade.entry_price
            side = trade.side
            size_usd = trade.size_usd
            next_funding_ts = signal.ticker.next_funding_ts
            opened_at = trade.opened_at

        # SL/TP в абсолютных ценах
        if side == "Buy":   # лонг
            tp_price = entry * (1 + self.tp_pct / 100)
            sl_price = entry * (1 - self.sl_pct / 100)
        else:               # шорт
            tp_price = entry * (1 - self.tp_pct / 100)
            sl_price = entry * (1 + self.sl_pct / 100)

        logger.info(
            f"{signal.symbol}: мониторинг TP={tp_price:.6f} SL={sl_price:.6f}"
        )

        funding_credited = False

        while True:
            await asyncio.sleep(3)

            mark = await self.client.get_mark_price(signal.symbol)
            if mark is None:
                continue

            import time
            now_ms = int(time.time() * 1000)
            age_sec = (datetime.utcnow() - opened_at).total_seconds()

            # Засчитать funding если время выплаты прошло
            if not funding_credited and now_ms >= next_funding_ts:
                funding_usd = size_usd * signal.expected_funding_pct / 100
                with get_session() as s:
                    t = s.get(SqueezeTrade, trade_id)
                    if t:
                        t.funding_received_usd = funding_usd
                        t.funding_paid_at = datetime.utcnow()
                funding_credited = True
                logger.info(f"{signal.symbol}: funding зачислен +${funding_usd:.4f}")

            # Проверяем условия выхода
            reason = None
            if side == "Buy":
                if mark >= tp_price:
                    reason = "tp"
                elif mark <= sl_price:
                    reason = "sl"
            else:
                if mark <= tp_price:
                    reason = "tp"
                elif mark >= sl_price:
                    reason = "sl"

            if age_sec >= self.max_age:
                reason = "timeout"

            # Закрываем после того как funding зачислен и есть причина
            # (или принудительно по timeout)
            if reason == "timeout" or (funding_credited and reason):
                await self._close(trade_id, mark, reason or "funding_paid")
                break

            # Если funding уже получен и нет открытой причины для держания — выходим
            if funding_credited and not reason:
                # Ждём ещё немного — может сработает squeeze
                if age_sec >= self.max_age * 0.5:
                    await self._close(trade_id, mark, "funding_paid")
                    break

    async def _close(self, trade_id: int, exit_price: float, reason: str):
        with get_session() as s:
            trade = s.get(SqueezeTrade, trade_id)
            if not trade or trade.closed_at:
                return

            entry = trade.entry_price
            side = trade.side
            size_usd = trade.size_usd
            size_coin = size_usd / entry

            # PnL от движения цены
            if side == "Buy":
                price_pnl = (exit_price - entry) * size_coin
            else:
                price_pnl = (entry - exit_price) * size_coin

            exit_fee = size_usd * self.client.taker_fee
            total_fees = (trade.fees_usd or 0) + exit_fee
            funding = trade.funding_received_usd or 0
            total_pnl = funding + price_pnl - total_fees

            trade.exit_price = exit_price
            trade.price_pnl_usd = price_pnl
            trade.fees_usd = total_fees
            trade.total_pnl_usd = total_pnl
            trade.close_reason = reason
            trade.closed_at = datetime.utcnow()

            symbol = trade.symbol

        logger.info(
            f"[{self.mode}] CLOSE {symbol} reason={reason} "
            f"funding=${funding:.4f} price=${price_pnl:+.4f} "
            f"fees=${total_fees:.4f} TOTAL=${total_pnl:+.4f}"
        )
        await self.notifier.closed(symbol, reason, funding, price_pnl, total_fees, total_pnl)
        self._active.pop(trade_id, None)
