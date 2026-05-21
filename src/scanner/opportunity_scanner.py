"""Сканер возможностей funding rate arbitrage.

Находит монеты где разница funding rates между биржами достаточно велика
для прибыльной арбитражной сделки.
"""
import asyncio
from dataclasses import dataclass
from loguru import logger

from src.exchanges.base import ExchangeBase, FundingInfo


@dataclass
class Opportunity:
    """Возможность для funding arb."""
    symbol: str
    # Биржа где открываем SHORT (там funding высокий и положительный)
    short_exchange: str
    short_rate_8h: float        # % за 8h
    # Биржа где открываем LONG (там funding низкий или отрицательный)
    long_exchange: str
    long_rate_8h: float
    # Чистый ожидаемый profit за 8h = short_rate - long_rate (в %)
    profit_per_8h_pct: float
    # Минимальный объём 24h из двух бирж
    min_volume_24h: float
    # Mark prices для расчёта basis
    short_mark: float
    long_mark: float

    @property
    def basis_pct(self) -> float:
        """Расхождение цены между биржами (basis risk)."""
        avg = (self.short_mark + self.long_mark) / 2
        if avg == 0:
            return 0.0
        return abs(self.short_mark - self.long_mark) / avg * 100

    @property
    def annual_pct(self) -> float:
        """Годовая доходность (3 выплаты в сутки * 365)."""
        return self.profit_per_8h_pct * 3 * 365


class OpportunityScanner:
    def __init__(
        self,
        exchanges: list[ExchangeBase],
        min_funding_diff_pct: float = 0.03,
        min_volume_24h_usd: float = 1_000_000,
        max_basis_pct: float = 0.5,
    ):
        if len(exchanges) < 2:
            raise ValueError("Сканер требует минимум 2 биржи")
        self.exchanges = exchanges
        self.min_diff = min_funding_diff_pct
        self.min_volume = min_volume_24h_usd
        self.max_basis = max_basis_pct

    async def _fetch_all(self) -> dict[str, list[FundingInfo]]:
        """Запросить funding rates со всех бирж параллельно."""
        results = await asyncio.gather(
            *[ex.get_all_funding_rates() for ex in self.exchanges],
            return_exceptions=True,
        )

        out: dict[str, list[FundingInfo]] = {}
        for ex, res in zip(self.exchanges, results):
            if isinstance(res, Exception):
                logger.error(f"{ex.name} failed: {res}")
                out[ex.name] = []
            else:
                out[ex.name] = res
        return out

    async def scan(self) -> list[Opportunity]:
        """Найти все возможности арбитража прямо сейчас."""
        rates_by_exchange = await self._fetch_all()

        # Строим индекс {symbol: {exchange_name: FundingInfo}}
        index: dict[str, dict[str, FundingInfo]] = {}
        for ex_name, rates in rates_by_exchange.items():
            for r in rates:
                index.setdefault(r.symbol, {})[ex_name] = r

        # Только монеты которые есть на обеих биржах
        common = {sym: data for sym, data in index.items() if len(data) >= 2}

        opportunities: list[Opportunity] = []
        for symbol, data in common.items():
            # Сравниваем все пары бирж
            ex_names = list(data.keys())
            for i in range(len(ex_names)):
                for j in range(i + 1, len(ex_names)):
                    a, b = data[ex_names[i]], data[ex_names[j]]

                    # Кто получает funding (шорт), кто платит (лонг)
                    if a.current_rate_8h > b.current_rate_8h:
                        short, long = a, b
                    else:
                        short, long = b, a

                    diff = short.current_rate_8h - long.current_rate_8h
                    if diff < self.min_diff:
                        continue

                    min_vol = min(short.volume_24h_usd, long.volume_24h_usd)
                    if min_vol < self.min_volume:
                        continue

                    op = Opportunity(
                        symbol=symbol,
                        short_exchange=short.exchange,
                        short_rate_8h=short.current_rate_8h,
                        long_exchange=long.exchange,
                        long_rate_8h=long.current_rate_8h,
                        profit_per_8h_pct=diff,
                        min_volume_24h=min_vol,
                        short_mark=short.mark_price,
                        long_mark=long.mark_price,
                    )

                    # Фильтр по basis risk
                    if op.basis_pct > self.max_basis:
                        continue

                    opportunities.append(op)

        opportunities.sort(key=lambda o: o.profit_per_8h_pct, reverse=True)
        return opportunities
