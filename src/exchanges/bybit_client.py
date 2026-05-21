"""Bybit V5 API адаптер.

Используем прямые HTTP запросы к публичным endpoints для получения
funding rates и orderbook. Аутентификация нужна только для balance/orders
(live режим — будет добавлено позже).
"""
import time
from typing import Optional
import aiohttp
from loguru import logger

from .base import ExchangeBase, FundingInfo, Orderbook


class BybitClient(ExchangeBase):
    name = "bybit"
    taker_fee = 0.00055           # 0.055% taker на linear perps
    funding_interval_hours = 8     # выплаты каждые 8 часов

    def __init__(self, api_key: str = "", api_secret: str = "", testnet: bool = False,
                 taker_fee: float | None = None):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = (
            "https://api-testnet.bybit.com" if testnet else "https://api.bybit.com"
        )
        if taker_fee is not None:
            self.taker_fee = taker_fee
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    @staticmethod
    def _normalize_symbol(bybit_symbol: str) -> str:
        """BTCUSDT → BTC (для сравнения между биржами)."""
        if bybit_symbol.endswith("USDT"):
            return bybit_symbol[:-4]
        return bybit_symbol

    @staticmethod
    def _to_bybit_symbol(symbol: str) -> str:
        """BTC → BTCUSDT."""
        if symbol.endswith("USDT"):
            return symbol
        return f"{symbol}USDT"

    async def get_all_funding_rates(self) -> list[FundingInfo]:
        """Получить funding по всем linear перпам.

        Bybit возвращает fundingRate уже в виде доли за 8 часов
        (например 0.0001 = 0.01% за 8 часов).
        """
        session = await self._get_session()
        url = f"{self.base_url}/v5/market/tickers"
        params = {"category": "linear"}

        try:
            async with session.get(url, params=params, timeout=10) as resp:
                data = await resp.json()
        except Exception as e:
            logger.error(f"Bybit get_all_funding_rates failed: {e}")
            return []

        if data.get("retCode") != 0:
            logger.error(f"Bybit API error: {data.get('retMsg')}")
            return []

        result: list[FundingInfo] = []
        for t in data["result"]["list"]:
            # Берём только USDT перпы
            sym = t.get("symbol", "")
            if not sym.endswith("USDT"):
                continue

            try:
                # Bybit funding rate уже за 8h → конвертируем в %
                rate_8h = float(t.get("fundingRate", 0)) * 100
                mark = float(t.get("markPrice", 0))
                # turnover24h в USDT
                volume = float(t.get("turnover24h", 0))
                next_ts = int(t.get("nextFundingTime", 0))
            except (ValueError, TypeError):
                continue

            if mark <= 0:
                continue

            result.append(FundingInfo(
                symbol=self._normalize_symbol(sym),
                exchange=self.name,
                current_rate_8h=rate_8h,
                predicted_rate_8h=rate_8h,  # Bybit V5 не отдаёт predicted отдельно
                next_funding_ts=next_ts,
                mark_price=mark,
                volume_24h_usd=volume,
            ))

        return result

    async def get_funding_rate(self, symbol: str) -> Optional[FundingInfo]:
        session = await self._get_session()
        url = f"{self.base_url}/v5/market/tickers"
        params = {"category": "linear", "symbol": self._to_bybit_symbol(symbol)}

        try:
            async with session.get(url, params=params, timeout=10) as resp:
                data = await resp.json()
        except Exception as e:
            logger.error(f"Bybit get_funding_rate({symbol}) failed: {e}")
            return None

        items = data.get("result", {}).get("list", [])
        if not items:
            return None

        t = items[0]
        try:
            return FundingInfo(
                symbol=self._normalize_symbol(t["symbol"]),
                exchange=self.name,
                current_rate_8h=float(t["fundingRate"]) * 100,
                predicted_rate_8h=float(t["fundingRate"]) * 100,
                next_funding_ts=int(t["nextFundingTime"]),
                mark_price=float(t["markPrice"]),
                volume_24h_usd=float(t.get("turnover24h", 0)),
            )
        except (KeyError, ValueError):
            return None

    async def get_orderbook(self, symbol: str) -> Optional[Orderbook]:
        session = await self._get_session()
        url = f"{self.base_url}/v5/market/orderbook"
        params = {
            "category": "linear",
            "symbol": self._to_bybit_symbol(symbol),
            "limit": 1,
        }

        try:
            async with session.get(url, params=params, timeout=10) as resp:
                data = await resp.json()
        except Exception as e:
            logger.error(f"Bybit get_orderbook({symbol}) failed: {e}")
            return None

        r = data.get("result", {})
        bids = r.get("b", [])
        asks = r.get("a", [])
        if not bids or not asks:
            return None

        try:
            return Orderbook(
                symbol=symbol,
                exchange=self.name,
                best_bid=float(bids[0][0]),
                best_ask=float(asks[0][0]),
                bid_qty=float(bids[0][1]),
                ask_qty=float(asks[0][1]),
            )
        except (ValueError, IndexError):
            return None

    async def get_balance(self) -> float:
        """Свободный USDT баланс. Требует API ключей.

        В paper режиме не вызывается — будет добавлено в Phase 8 (live).
        """
        if not self.api_key:
            return 0.0
        # TODO: реализовать в Phase 8 (live trading)
        logger.warning("Bybit.get_balance(): live режим ещё не реализован")
        return 0.0
