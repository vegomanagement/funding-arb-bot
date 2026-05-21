"""Унифицированный интерфейс для бирж"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class FundingInfo:
    """Информация о funding rate монеты на бирже.

    Все ставки нормализованы к % за 8 часов для сравнения между биржами.
    Hyperliquid платит каждый час → его ставка умножается на 8.
    """
    symbol: str               # унифицированный (например "BTC")
    exchange: str             # "bybit" / "hyperliquid"
    current_rate_8h: float    # % за 8 часов (нормализовано)
    predicted_rate_8h: float  # predicted % за 8 часов
    next_funding_ts: int      # unix ms времени следующей выплаты
    mark_price: float
    volume_24h_usd: float


@dataclass
class Orderbook:
    symbol: str
    exchange: str
    best_bid: float
    best_ask: float
    bid_qty: float
    ask_qty: float

    @property
    def mid(self) -> float:
        return (self.best_bid + self.best_ask) / 2

    @property
    def spread_pct(self) -> float:
        return (self.best_ask - self.best_bid) / self.mid * 100


@dataclass
class Position:
    symbol: str
    exchange: str
    side: str       # "long" / "short"
    size: float     # размер в монетах
    entry_price: float
    unrealized_pnl: float


@dataclass
class Order:
    order_id: str
    symbol: str
    side: str
    size: float
    avg_price: float
    fee: float
    status: str


class ExchangeBase(ABC):
    """Базовый класс для адаптеров бирж."""

    name: str = ""
    # Комиссия taker (доля, не процент): 0.00055 = 0.055%
    taker_fee: float = 0.0
    # Период выплаты funding в часах
    funding_interval_hours: int = 8

    @abstractmethod
    async def get_all_funding_rates(self) -> list[FundingInfo]:
        """Получить funding rates по всем доступным перпам."""

    @abstractmethod
    async def get_funding_rate(self, symbol: str) -> Optional[FundingInfo]:
        """Получить funding rate одной монеты."""

    @abstractmethod
    async def get_orderbook(self, symbol: str) -> Optional[Orderbook]:
        """Получить лучшие bid/ask."""

    @abstractmethod
    async def get_balance(self) -> float:
        """Получить свободный USDT баланс."""

    async def get_position(self, symbol: str) -> Optional[Position]:
        """Получить открытую позицию (опционально для live режима)."""
        return None

    async def close(self) -> None:
        """Закрыть HTTP сессии."""
