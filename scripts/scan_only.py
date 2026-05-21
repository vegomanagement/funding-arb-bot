"""Standalone скрипт — только сканирует возможности, не торгует.

Запуск:
    python scripts/scan_only.py

Полезно для:
  - Проверки что сканер работает
  - Изучения реальных funding rates в моменте
  - Сбора статистики какие монеты дают арбитраж
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import load_config
from src.exchanges.bybit_client import BybitClient
from src.exchanges.hyperliquid_client import HyperliquidClient
from src.market_data.cache import MarketDataCache
from src.scanner.opportunity_scanner import OpportunityScanner


async def main():
    cfg = load_config()
    print(f"Mode: {cfg.mode}")
    print(f"Min funding diff: {cfg.min_funding_diff_pct}% (за 8h)")
    print(f"Min volume 24h: ${cfg.min_volume_24h_usd:,.0f}")
    print(f"Scan interval: {cfg.scan_interval_sec}s")
    print("─" * 80)

    bybit = BybitClient()
    hl = HyperliquidClient()
    cache = MarketDataCache([bybit, hl])
    scanner = OpportunityScanner(
        min_funding_diff_pct=cfg.min_funding_diff_pct,
        min_volume_24h_usd=cfg.min_volume_24h_usd,
    )

    try:
        while True:
            await cache.refresh()
            opportunities = scanner.scan(cache)
            if not opportunities:
                print(f"\rНет возможностей. Жду {cfg.scan_interval_sec}s...", end="", flush=True)
            else:
                print(f"\n\n🟢 Найдено {len(opportunities)} возможностей:")
                print(f"{'Symbol':<10} {'SHORT':<14} {'LONG':<14} {'Profit/8h':>10} "
                      f"{'APR':>8} {'Basis':>7} {'MinVol24h':>14}")
                print("─" * 80)
                for op in opportunities[:15]:
                    short_str = f"{op.short_exchange}({op.short_rate_8h:+.3f}%)"
                    long_str = f"{op.long_exchange}({op.long_rate_8h:+.3f}%)"
                    print(
                        f"{op.symbol:<10} {short_str:<14} {long_str:<14} "
                        f"{op.profit_per_8h_pct:>9.3f}% {op.annual_pct:>7.0f}% "
                        f"{op.basis_pct:>6.2f}% ${op.min_volume_24h:>13,.0f}"
                    )

            await asyncio.sleep(cfg.scan_interval_sec)
    except KeyboardInterrupt:
        print("\nОстановлено пользователем.")
    finally:
        await bybit.close()
        await hl.close()


if __name__ == "__main__":
    asyncio.run(main())
