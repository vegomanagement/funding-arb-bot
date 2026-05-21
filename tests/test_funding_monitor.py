"""Тесты FundingMonitor — симуляция выплат, логика закрытий."""
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from src.database.db import get_session
from src.database.models import Position, FundingEvent
from src.exchanges.base import FundingInfo, ExchangeBase
from src.execution.paper_trader import PaperTrader
from src.exchanges.base import Orderbook
from src.market_data.cache import MarketDataCache
from src.monitoring.funding_monitor import FundingMonitor
from src.strategy.risk_manager import RiskManager
from src.notifications.telegram_notifier import TelegramNotifier


def make_funding_info(symbol, exchange, rate_8h, mark=100.0):
    return FundingInfo(
        symbol=symbol, exchange=exchange,
        current_rate_8h=rate_8h, predicted_rate_8h=rate_8h,
        next_funding_ts=0, mark_price=mark, volume_24h_usd=5_000_000,
    )


def make_cache(short_rate=0.05, long_rate=0.01, mark=100.0):
    cache = MagicMock(spec=MarketDataCache)
    cache.get_funding.side_effect = lambda ex, sym: (
        make_funding_info(sym, "bybit", short_rate, mark) if ex == "bybit"
        else make_funding_info(sym, "hyperliquid", long_rate, mark)
    )
    return cache


def make_exchange_stub(name, taker_fee=0.0005, interval_hours=8):
    ex = MagicMock(spec=ExchangeBase)
    ex.name = name
    ex.taker_fee = taker_fee
    ex.funding_interval_hours = interval_hours
    ex.get_orderbook = AsyncMock(return_value=Orderbook(
        symbol="BTC", exchange=name,
        best_bid=100.0, best_ask=100.0, bid_qty=10.0, ask_qty=10.0,
    ))
    return ex


def make_monitor(mode="paper", min_diff=0.03):
    bybit = make_exchange_stub("bybit")
    hl = make_exchange_stub("hyperliquid", interval_hours=1)
    notifier = MagicMock(spec=TelegramNotifier)
    notifier.funding_paid = AsyncMock()
    notifier.closed = AsyncMock()
    risk = RiskManager(capital_usd=1000, max_positions=3, max_position_pct=30,
                       stop_loss_pct=2, daily_drawdown_limit_pct=5)
    trader = PaperTrader({"bybit": bybit, "hyperliquid": hl}, mode=mode)
    monitor = FundingMonitor(
        exchanges={"bybit": bybit, "hyperliquid": hl},
        trader=trader,
        risk=risk,
        notifier=notifier,
        min_funding_diff_pct=min_diff,
        mode=mode,
    )
    return monitor, trader, bybit, hl, notifier


async def open_test_position(trader, symbol="BTC", mode="paper"):
    from src.scanner.opportunity_scanner import Opportunity
    op = Opportunity(
        symbol=symbol, short_exchange="bybit", short_rate_8h=0.05,
        long_exchange="hyperliquid", long_rate_8h=0.01,
        profit_per_8h_pct=0.04, min_volume_24h=5_000_000,
        short_mark=100.0, long_mark=100.0,
    )
    return await trader.open(op, size_usd=1000)


# ── Симуляция funding выплат ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_funding_before_period():
    monitor, trader, bybit, hl, notifier = make_monitor()
    pos_id = await open_test_position(trader)

    cache = make_cache()
    await monitor.check_all(cache)

    with get_session() as s:
        events = s.query(FundingEvent).filter_by(position_id=pos_id).all()
    # Первая проверка сразу после открытия — период ещё не прошёл
    assert len(events) == 0


@pytest.mark.asyncio
async def test_funding_paid_after_8h():
    """Funding должен начисляться через 8h после открытия позиции."""
    monitor, trader, bybit, hl, notifier = make_monitor()
    pos_id = await open_test_position(trader)

    # Подменяем opened_at так, чтобы прошло 9 часов
    past = datetime.utcnow() - timedelta(hours=9)
    with get_session() as s:
        pos = s.get(Position, pos_id)
        pos.opened_at = past

    cache = make_cache(short_rate=0.05, long_rate=0.01)
    await monitor.check_all(cache)

    with get_session() as s:
        events = s.query(FundingEvent).all()
        exchanges = [e.exchange for e in events]
    # Должны быть события и для bybit (8h) и для hyperliquid (1h × 9 = 9 events)
    assert len(exchanges) > 0
    assert exchanges.count("bybit") == 1  # 1 период по 8h


@pytest.mark.asyncio
async def test_no_double_funding_on_second_check():
    """После начисления второй check не добавляет новые события до следующего периода."""
    monitor, trader, bybit, hl, notifier = make_monitor()
    pos_id = await open_test_position(trader)

    past = datetime.utcnow() - timedelta(hours=9)
    with get_session() as s:
        pos = s.get(Position, pos_id)
        pos.opened_at = past

    cache = make_cache()
    await monitor.check_all(cache)  # первый check → funding начислен

    with get_session() as s:
        count_after_first = s.query(FundingEvent).filter_by(exchange="bybit").count()

    await monitor.check_all(cache)  # второй check сразу → без новых событий

    with get_session() as s:
        count_after_second = s.query(FundingEvent).filter_by(exchange="bybit").count()

    assert count_after_first == count_after_second  # нет дублей


# ── Логика закрытий ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_closes_on_funding_flip():
    monitor, trader, _, __, notifier = make_monitor(min_diff=0.03)
    pos_id = await open_test_position(trader)

    # Funding перевернулся: теперь HL > Bybit
    cache = make_cache(short_rate=0.01, long_rate=0.05)
    await monitor.check_all(cache)

    with get_session() as s:
        pos = s.get(Position, pos_id)
        status, reason = pos.status, pos.close_reason
    assert status == "closed"
    assert reason == "funding_flipped"


@pytest.mark.asyncio
async def test_closes_on_funding_too_low():
    monitor, trader, _, __, notifier = make_monitor(min_diff=0.03)
    pos_id = await open_test_position(trader)

    # Diff упал ниже половины порога (min_diff * 0.5 = 0.015)
    cache = make_cache(short_rate=0.02, long_rate=0.01)  # diff=0.01 < 0.015
    await monitor.check_all(cache)

    with get_session() as s:
        pos = s.get(Position, pos_id)
        status, reason = pos.status, pos.close_reason
    assert status == "closed"
    assert reason == "funding_low"


@pytest.mark.asyncio
async def test_closes_on_stop_loss():
    monitor, trader, bybit, hl, notifier = make_monitor()
    pos_id = await open_test_position(trader)

    # Монитор пересчитывает total_pnl = funding + unrealized - fees_paid.
    # Чтобы сработал стоп-лосс (2% от $1000 = -$20), выставляем fees_paid = $50.
    with get_session() as s:
        pos = s.get(Position, pos_id)
        pos.fees_paid_usd = 50.0

    cache = make_cache()
    await monitor.check_all(cache)

    with get_session() as s:
        pos = s.get(Position, pos_id)
        status, reason = pos.status, pos.close_reason
    assert status == "closed"
    assert reason == "stop_loss"


@pytest.mark.asyncio
async def test_closes_on_wide_basis():
    monitor, trader, _, __, notifier = make_monitor()
    pos_id = await open_test_position(trader)

    # Basis > 0.5%: цены разошлись
    cache = make_cache(short_rate=0.05, long_rate=0.01, mark=None)
    # Вручную делаем get_funding возвращать разные mark prices
    cache.get_funding.side_effect = lambda ex, sym: (
        make_funding_info(sym, "bybit", 0.05, mark=100.0) if ex == "bybit"
        else make_funding_info(sym, "hyperliquid", 0.01, mark=101.0)
    )
    await monitor.check_all(cache)

    with get_session() as s:
        pos = s.get(Position, pos_id)
        status, reason = pos.status, pos.close_reason
    assert status == "closed"
    assert reason == "basis_wide"


@pytest.mark.asyncio
async def test_healthy_position_stays_open():
    monitor, trader, _, __, notifier = make_monitor()
    pos_id = await open_test_position(trader)

    cache = make_cache(short_rate=0.05, long_rate=0.01)
    await monitor.check_all(cache)

    with get_session() as s:
        pos = s.get(Position, pos_id)
        status = pos.status
    assert status == "open"
