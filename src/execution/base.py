from abc import ABC, abstractmethod


class TraderBase(ABC):
    @abstractmethod
    async def open(self, op, size_usd: float) -> int | None:
        """Открыть хеджированную позицию. Возвращает id в БД или None."""

    @abstractmethod
    async def close(self, position_id: int, reason: str) -> float | None:
        """Закрыть позицию. Возвращает итоговый PnL или None при ошибке."""

    @abstractmethod
    def record_funding(
        self,
        position_id: int,
        exchange: str,
        amount_usd: float,
        rate_pct: float,
        period_hours: int,
    ) -> None:
        """Записать выплату/получение funding в БД."""
