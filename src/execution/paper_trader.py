"""Paper trader — симуляция сделок без реальных ордеров.

Использует реальные цены и orderbook'и для симуляции slippage и комиссий.
Записывает все сделки в БД с пометкой mode='paper'.
"""
from datetime import datetime
from loguru import logger

from src.database.db import get_session
from src.database.models import Position, FundingEvent
from src.exchanges.base import ExchangeBase, Orderbook
from src.execution.base import TraderBase
from src.scanner.opportunity_scanner import Opportunity


class PaperTrader(TraderBase):
    def __init__(self, exchanges: dict[str, ExchangeBase], mode: str = "paper"):
        # {"bybit": BybitClient, "hyperliquid": HyperliquidClient}
        self.exchanges = exchanges
        self.mode = mode

    @staticmethod
    def _slippage_price(ob: Orderbook, side: str) -> float:
        """Цена с учётом slippage при маркет-входе.

        Buy → берём по ask
        Sell → отдаём по bid
        """
        return ob.best_ask if side == "buy" else ob.best_bid

    async def open(self, op: Opportunity, size_usd: float) -> int | None:
        """Открыть хеджированную позицию (short + long).

        Возвращает id позиции в БД или None при ошибке.
        """
        short_ex = self.exchanges[op.short_exchange]
        long_ex = self.exchanges[op.long_exchange]

        # Получаем orderbook'и для расчёта реальной цены входа
        ob_short, ob_long = await short_ex.get_orderbook(op.symbol), await long_ex.get_orderbook(op.symbol)
        if not ob_short or not ob_long:
            logger.warning(f"{op.symbol}: orderbook недоступен на {op.short_exchange} или {op.long_exchange}")
            return None

        # Symmetric entry: продаём по bid на short_ex, покупаем по ask на long_ex
        short_price = ob_short.best_bid
        long_price = ob_long.best_ask

        # Размер в монетах считаем по средней цене
        avg_price = (short_price + long_price) / 2
        if avg_price <= 0:
            return None
        size_coin = size_usd / avg_price

        # Комиссии (taker)
        short_fee = size_usd * short_ex.taker_fee
        long_fee = size_usd * long_ex.taker_fee
        total_fees = short_fee + long_fee

        with get_session() as s:
            pos = Position(
                symbol=op.symbol,
                mode=self.mode,
                status="open",
                opened_at=datetime.utcnow(),
                long_exchange=op.long_exchange,
                short_exchange=op.short_exchange,
                size_usd=size_usd,
                size_coin=size_coin,
                entry_long_price=long_price,
                entry_short_price=short_price,
                entry_funding_diff_pct=op.profit_per_8h_pct,
                fees_paid_usd=total_fees,
                total_pnl_usd=-total_fees,
            )
            s.add(pos)
            s.flush()
            pos_id = pos.id

        logger.info(
            f"[{self.mode}] OPEN {op.symbol} size=${size_usd:.2f} "
            f"SHORT {op.short_exchange}@{short_price:.6f} LONG {op.long_exchange}@{long_price:.6f} "
            f"fees=${total_fees:.4f}"
        )
        return pos_id

    async def close(self, position_id: int, reason: str) -> float | None:
        """Закрыть позицию. Возвращает итоговый PnL."""
        with get_session() as s:
            pos = s.get(Position, position_id)
            if not pos or pos.status != "open":
                return None

            short_ex = self.exchanges[pos.short_exchange]
            long_ex = self.exchanges[pos.long_exchange]
            ob_short = await short_ex.get_orderbook(pos.symbol)
            ob_long = await long_ex.get_orderbook(pos.symbol)
            if not ob_short or not ob_long:
                logger.error(f"Не могу закрыть {pos.symbol}: orderbook недоступен")
                return None

            # Выход: покупаем обратно short по ask, продаём long по bid
            short_exit = ob_short.best_ask
            long_exit = ob_long.best_bid

            # PnL по ценам:
            #   short PnL = (entry - exit) * size_coin
            #   long PnL  = (exit - entry) * size_coin
            short_pnl = (pos.entry_short_price - short_exit) * pos.size_coin
            long_pnl = (long_exit - pos.entry_long_price) * pos.size_coin
            price_pnl = short_pnl + long_pnl

            # Комиссии на выход
            exit_fees = pos.size_usd * (short_ex.taker_fee + long_ex.taker_fee)
            total_fees = (pos.fees_paid_usd or 0) + exit_fees

            total_pnl = (pos.funding_received_usd or 0) + price_pnl - total_fees

            pos.status = "closed"
            pos.closed_at = datetime.utcnow()
            pos.exit_short_price = short_exit
            pos.exit_long_price = long_exit
            pos.price_pnl_usd = price_pnl
            pos.fees_paid_usd = total_fees
            pos.total_pnl_usd = total_pnl
            pos.close_reason = reason

            logger.info(
                f"[{self.mode}] CLOSE {pos.symbol} reason={reason} "
                f"funding=${pos.funding_received_usd or 0:.4f} price_pnl=${price_pnl:+.4f} "
                f"fees=${total_fees:.4f} TOTAL=${total_pnl:+.4f}"
            )
            return total_pnl

    def record_funding(self, position_id: int, exchange: str, amount_usd: float,
                       rate_pct: float, period_hours: int) -> None:
        """Записать получение/выплату funding в БД."""
        with get_session() as s:
            pos = s.get(Position, position_id)
            if not pos:
                return
            ev = FundingEvent(
                position_id=position_id,
                exchange=exchange,
                paid_at=datetime.utcnow(),
                amount_usd=amount_usd,
                rate_pct=rate_pct,
                period_hours=period_hours,
            )
            s.add(ev)
            pos.funding_received_usd = (pos.funding_received_usd or 0) + amount_usd
            pos.total_pnl_usd = (pos.total_pnl_usd or 0) + amount_usd
