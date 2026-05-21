"""Центральный кеш рыночных данных.

Один вызов refresh() запрашивает все биржи параллельно.
Scanner и FundingMonitor читают из кеша — не делают собственных запросов к API.
"""
import asyncio
from datetime import datetime
from loguru import logger

from src.exchanges.base import ExchangeBase, FundingInfo


class MarketDataCache:
    def __init__(self, exchanges: list[ExchangeBase]):
        self._exchanges: dict[str, ExchangeBase] = {ex.name: ex for ex in exchanges}
        # {exchange_name: {symbol: FundingInfo}}
        self._data: dict[str, dict[str, FundingInfo]] = {ex.name: {} for ex in exchanges}
        self.last_updated: datetime | None = None

    async def refresh(self) -> None:
        """Запросить все биржи параллельно и обновить кеш."""
        results = await asyncio.gather(
            *[ex.get_all_funding_rates() for ex in self._exchanges.values()],
            return_exceptions=True,
        )
        for ex_name, res in zip(self._exchanges, results):
            if isinstance(res, Exception):
                logger.error(f"MarketDataCache: {ex_name} ошибка при обновлении: {res}")
                # Старые данные остаются — не затираем
            else:
                self._data[ex_name] = {info.symbol: info for info in res}
                logger.debug(f"MarketDataCache: {ex_name} — {len(res)} монет")
        self.last_updated = datetime.utcnow()

    def get_funding(self, exchange: str, symbol: str) -> FundingInfo | None:
        return self._data.get(exchange, {}).get(symbol)

    def get_all(self, exchange: str) -> list[FundingInfo]:
        return list(self._data.get(exchange, {}).values())

    def exchange_names(self) -> list[str]:
        return list(self._exchanges.keys())
