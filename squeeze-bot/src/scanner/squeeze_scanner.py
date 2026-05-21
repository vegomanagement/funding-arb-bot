"""Сканер для funding squeeze стратегии.

Ищет монеты где:
  1. |funding rate| > порога (например 0.3% за 8h)
  2. До выплаты осталось меньше entry_before_sec секунд

Логика входа:
  funding > 0  → лонгисты платят шортистам → открываем ШОРТ
  funding < 0  → шортисты платят лонгистам → открываем ЛОНГ
"""
from dataclasses import dataclass
from src.exchanges.bybit_client import BybitClient, Ticker


@dataclass
class SqueezeSignal:
    ticker: Ticker
    side: str           # "Buy" (лонг) или "Sell" (шорт)
    expected_funding_pct: float  # сколько % получим от funding
    seconds_left: int

    @property
    def symbol(self): return self.ticker.symbol
    @property
    def mark_price(self): return self.ticker.mark_price


class SqueezeScanner:
    def __init__(
        self,
        client: BybitClient,
        min_funding_abs_pct: float = 0.3,
        entry_before_sec: int = 45,
        min_volume_usd: float = 5_000_000,
    ):
        self.client = client
        self.min_funding = min_funding_abs_pct
        self.entry_before = entry_before_sec
        self.min_volume = min_volume_usd

    async def scan(self) -> list[SqueezeSignal]:
        tickers = await self.client.get_all_tickers()

        signals = []
        for t in tickers:
            # Проверяем порог funding
            if abs(t.funding_rate_pct) < self.min_funding:
                continue

            # Проверяем ликвидность
            if t.volume_24h_usd < self.min_volume:
                continue

            # Проверяем время до выплаты
            secs = t.seconds_to_funding
            if secs > self.entry_before or secs <= 0:
                continue

            # Направление входа
            # funding < 0 → шортисты платят лонгистам → нам выгодно быть в лонге
            # funding > 0 → лонгисты платят → нам выгодно быть в шорте
            side = "Buy" if t.funding_rate_pct < 0 else "Sell"

            signals.append(SqueezeSignal(
                ticker=t,
                side=side,
                expected_funding_pct=abs(t.funding_rate_pct),
                seconds_left=secs,
            ))

        # Сортируем по размеру funding — берём самые жирные
        signals.sort(key=lambda s: s.expected_funding_pct, reverse=True)
        return signals
