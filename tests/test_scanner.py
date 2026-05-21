"""Тесты сканера возможностей."""
import pytest
from unittest.mock import MagicMock

from src.exchanges.base import FundingInfo
from src.market_data.cache import MarketDataCache
from src.scanner.opportunity_scanner import OpportunityScanner


def make_info(symbol, exchange, rate_8h, mark=100.0, volume=5_000_000):
    return FundingInfo(
        symbol=symbol,
        exchange=exchange,
        current_rate_8h=rate_8h,
        predicted_rate_8h=rate_8h,
        next_funding_ts=0,
        mark_price=mark,
        volume_24h_usd=volume,
    )


def make_cache(bybit_rates: list[FundingInfo], hl_rates: list[FundingInfo]) -> MarketDataCache:
    cache = MagicMock(spec=MarketDataCache)
    cache.exchange_names.return_value = ["bybit", "hyperliquid"]
    cache.get_all.side_effect = lambda ex: bybit_rates if ex == "bybit" else hl_rates
    return cache


def scanner(min_diff=0.03, min_vol=1_000_000):
    return OpportunityScanner(min_funding_diff_pct=min_diff, min_volume_24h_usd=min_vol)


# ── Основные сценарии ────────────────────────────────────────────────────────

def test_finds_clear_opportunity():
    cache = make_cache(
        [make_info("BTC", "bybit", rate_8h=0.05)],
        [make_info("BTC", "hyperliquid", rate_8h=0.01)],
    )
    ops = scanner().scan(cache)
    assert len(ops) == 1
    op = ops[0]
    assert op.symbol == "BTC"
    assert op.short_exchange == "bybit"
    assert op.long_exchange == "hyperliquid"
    assert abs(op.profit_per_8h_pct - 0.04) < 1e-9


def test_short_goes_to_higher_rate():
    """Биржа с бо́льшим rate должна быть short_exchange."""
    cache = make_cache(
        [make_info("ETH", "bybit", rate_8h=0.01)],
        [make_info("ETH", "hyperliquid", rate_8h=0.08)],
    )
    ops = scanner().scan(cache)
    assert len(ops) == 1
    assert ops[0].short_exchange == "hyperliquid"
    assert ops[0].long_exchange == "bybit"


def test_returns_empty_when_diff_too_small():
    cache = make_cache(
        [make_info("BTC", "bybit", rate_8h=0.04)],
        [make_info("BTC", "hyperliquid", rate_8h=0.03)],  # diff=0.01 < min 0.03
    )
    assert scanner().scan(cache) == []


def test_returns_empty_when_volume_too_low():
    cache = make_cache(
        [make_info("BTC", "bybit", rate_8h=0.05, volume=500_000)],
        [make_info("BTC", "hyperliquid", rate_8h=0.01, volume=500_000)],
    )
    assert scanner(min_vol=1_000_000).scan(cache) == []


def test_filters_high_basis():
    """Если цены на биржах расходятся > max_basis_pct — отфильтровать."""
    cache = make_cache(
        [make_info("BTC", "bybit", rate_8h=0.05, mark=100.0)],
        [make_info("BTC", "hyperliquid", rate_8h=0.01, mark=101.0)],  # basis=1%
    )
    assert OpportunityScanner(min_funding_diff_pct=0.03, max_basis_pct=0.5).scan(cache) == []


def test_sorted_by_profit_descending():
    cache = make_cache(
        [
            make_info("BTC", "bybit", rate_8h=0.10),
            make_info("ETH", "bybit", rate_8h=0.05),
        ],
        [
            make_info("BTC", "hyperliquid", rate_8h=0.01),
            make_info("ETH", "hyperliquid", rate_8h=0.01),
        ],
    )
    ops = scanner().scan(cache)
    assert len(ops) == 2
    assert ops[0].profit_per_8h_pct > ops[1].profit_per_8h_pct


def test_skips_symbol_not_on_both_exchanges():
    cache = make_cache(
        [make_info("BTC", "bybit", rate_8h=0.05)],
        [make_info("SOL", "hyperliquid", rate_8h=0.01)],  # разные символы
    )
    assert scanner().scan(cache) == []


def test_annual_pct_calculation():
    cache = make_cache(
        [make_info("BTC", "bybit", rate_8h=0.10)],
        [make_info("BTC", "hyperliquid", rate_8h=0.01)],
    )
    op = scanner().scan(cache)[0]
    # 0.09% * 3 * 365 = 98.55%
    assert abs(op.annual_pct - 0.09 * 3 * 365) < 0.01
