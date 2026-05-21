"""Тест очистки старых записей opportunities."""
from datetime import datetime, timedelta

from src.database.db import get_session, cleanup_opportunities
from src.database.models import OpportunityLog


def make_opportunity(days_ago: float) -> None:
    with get_session() as s:
        s.add(OpportunityLog(
            found_at=datetime.utcnow() - timedelta(days=days_ago),
            symbol="BTC",
            short_exchange="bybit",
            short_rate_8h=0.05,
            long_exchange="hyperliquid",
            long_rate_8h=0.01,
            profit_per_8h_pct=0.04,
            basis_pct=0.1,
            min_volume_24h=5_000_000,
            taken=False,
        ))


def count_opportunities() -> int:
    with get_session() as s:
        return s.query(OpportunityLog).count()


def test_deletes_old_records():
    make_opportunity(days_ago=10)   # старая — должна удалиться
    make_opportunity(days_ago=5)    # свежая — остаётся
    make_opportunity(days_ago=1)    # свежая — остаётся

    deleted = cleanup_opportunities(keep_days=7)

    assert deleted == 1
    assert count_opportunities() == 2


def test_keeps_all_fresh_records():
    make_opportunity(days_ago=1)
    make_opportunity(days_ago=3)

    deleted = cleanup_opportunities(keep_days=7)

    assert deleted == 0
    assert count_opportunities() == 2


def test_empty_table_is_safe():
    deleted = cleanup_opportunities(keep_days=7)
    assert deleted == 0


def test_cleanup_is_idempotent():
    make_opportunity(days_ago=10)

    cleanup_opportunities(keep_days=7)
    deleted_second = cleanup_opportunities(keep_days=7)

    assert deleted_second == 0
    assert count_opportunities() == 0
