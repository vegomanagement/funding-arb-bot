"""Тесты PaperTrader — открытие/закрытие позиций, расчёт PnL."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from src.database.db import get_session
from src.database.models import Position, FundingEvent
from src.exchanges.base import Orderbook
from src.execution.paper_trader import PaperTrader
from src.scanner.opportunity_scanner import Opportunity


def make_opportunity(symbol="BTC", short_ex="bybit", long_ex="hyperliquid",
                     short_rate=0.05, long_rate=0.01,
                     short_mark=100.0, long_mark=100.0):
    return Opportunity(
        symbol=symbol,
        short_exchange=short_ex,
        short_rate_8h=short_rate,
        long_exchange=long_ex,
        long_rate_8h=long_rate,
        profit_per_8h_pct=short_rate - long_rate,
        min_volume_24h=5_000_000,
        short_mark=short_mark,
        long_mark=long_mark,
    )


def make_orderbook(exchange, symbol, bid=99.9, ask=100.1):
    return Orderbook(
        symbol=symbol, exchange=exchange,
        best_bid=bid, best_ask=ask, bid_qty=10.0, ask_qty=10.0,
    )


def make_trader(mode="paper"):
    bybit = AsyncMock()
    hl = AsyncMock()
    bybit.name = "bybit"
    bybit.taker_fee = 0.00055
    hl.name = "hyperliquid"
    hl.taker_fee = 0.00045
    return PaperTrader({"bybit": bybit, "hyperliquid": hl}, mode=mode), bybit, hl


# ── open ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_open_creates_position_in_db():
    trader, bybit, hl = make_trader()
    bybit.get_orderbook = AsyncMock(return_value=make_orderbook("bybit", "BTC"))
    hl.get_orderbook = AsyncMock(return_value=make_orderbook("hyperliquid", "BTC"))

    op = make_opportunity()
    pos_id = await trader.open(op, size_usd=300)

    assert pos_id is not None
    with get_session() as s:
        pos = s.get(Position, pos_id)
        assert pos.symbol == "BTC"
        assert pos.status == "open"
        assert pos.size_usd == 300
        assert pos.short_exchange == "bybit"
        assert pos.long_exchange == "hyperliquid"


@pytest.mark.asyncio
async def test_open_uses_bid_for_short_ask_for_long():
    """SHORT открывается по bid, LONG по ask — это реальные цены исполнения."""
    trader, bybit, hl = make_trader()
    bybit.get_orderbook = AsyncMock(return_value=make_orderbook("bybit", "BTC", bid=99.5, ask=100.5))
    hl.get_orderbook = AsyncMock(return_value=make_orderbook("hyperliquid", "BTC", bid=99.0, ask=101.0))

    op = make_opportunity()
    pos_id = await trader.open(op, size_usd=300)

    with get_session() as s:
        pos = s.get(Position, pos_id)
        assert pos.entry_short_price == 99.5   # bid на bybit
        assert pos.entry_long_price == 101.0   # ask на hyperliquid


@pytest.mark.asyncio
async def test_open_deducts_entry_fees():
    trader, bybit, hl = make_trader()
    bybit.get_orderbook = AsyncMock(return_value=make_orderbook("bybit", "BTC"))
    hl.get_orderbook = AsyncMock(return_value=make_orderbook("hyperliquid", "BTC"))

    pos_id = await trader.open(make_opportunity(), size_usd=1000)

    with get_session() as s:
        pos = s.get(Position, pos_id)
        expected_fees = 1000 * (0.00055 + 0.00045)  # = $1.00
        assert abs(pos.fees_paid_usd - expected_fees) < 1e-9
        assert pos.total_pnl_usd == -expected_fees


@pytest.mark.asyncio
async def test_open_returns_none_if_orderbook_unavailable():
    trader, bybit, hl = make_trader()
    bybit.get_orderbook = AsyncMock(return_value=None)
    hl.get_orderbook = AsyncMock(return_value=make_orderbook("hyperliquid", "BTC"))

    pos_id = await trader.open(make_opportunity(), size_usd=300)
    assert pos_id is None


# ── close ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_close_calculates_pnl():
    trader, bybit, hl = make_trader()
    bybit.get_orderbook = AsyncMock(return_value=make_orderbook("bybit", "BTC", bid=100.0, ask=100.0))
    hl.get_orderbook = AsyncMock(return_value=make_orderbook("hyperliquid", "BTC", bid=100.0, ask=100.0))

    pos_id = await trader.open(make_opportunity(), size_usd=1000)

    # Цена выросла: short в убытке, long в плюсе → дельта-нейтральность
    bybit.get_orderbook = AsyncMock(return_value=make_orderbook("bybit", "BTC", bid=110.0, ask=110.0))
    hl.get_orderbook = AsyncMock(return_value=make_orderbook("hyperliquid", "BTC", bid=110.0, ask=110.0))

    pnl = await trader.close(pos_id, reason="test")

    with get_session() as s:
        pos = s.get(Position, pos_id)
        assert pos.status == "closed"
        assert pos.close_reason == "test"
        # Дельта-нейтральная позиция: price PnL ≈ 0 (short и long отменяют друг друга)
        # Итог = funding(0) + price_pnl(≈0) - fees(>0) → небольшой минус от комиссий
        assert pos.total_pnl_usd < 0  # только комиссии


@pytest.mark.asyncio
async def test_close_idempotent():
    """Повторный вызов close на закрытой позиции возвращает None."""
    trader, bybit, hl = make_trader()
    bybit.get_orderbook = AsyncMock(return_value=make_orderbook("bybit", "BTC"))
    hl.get_orderbook = AsyncMock(return_value=make_orderbook("hyperliquid", "BTC"))

    pos_id = await trader.open(make_opportunity(), size_usd=300)
    await trader.close(pos_id, reason="first")
    result = await trader.close(pos_id, reason="second")
    assert result is None


# ── record_funding ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_record_funding_updates_position():
    trader, bybit, hl = make_trader()
    bybit.get_orderbook = AsyncMock(return_value=make_orderbook("bybit", "BTC"))
    hl.get_orderbook = AsyncMock(return_value=make_orderbook("hyperliquid", "BTC"))

    pos_id = await trader.open(make_opportunity(), size_usd=1000)

    trader.record_funding(pos_id, exchange="bybit", amount_usd=5.0, rate_pct=0.05, period_hours=8)

    with get_session() as s:
        pos = s.get(Position, pos_id)
        assert pos.funding_received_usd == 5.0
        events = s.query(FundingEvent).filter_by(position_id=pos_id).all()
        assert len(events) == 1
        assert events[0].amount_usd == 5.0
        assert events[0].exchange == "bybit"
