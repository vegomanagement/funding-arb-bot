"""Bybit клиент для squeeze бота.

Нам нужно только:
  - get_all_tickers()     — funding rates + время до выплаты
  - get_mark_price()      — текущая цена для расчёта SL/TP
"""
import time
from dataclasses import dataclass
from typing import Optional
import aiohttp
from loguru import logger


@dataclass
class Ticker:
    symbol: str            # BTCUSDT
    coin: str              # BTC
    funding_rate_pct: float  # % за 8h (нормализовано), знак сохранён
    next_funding_ts: int   # unix ms
    mark_price: float
    volume_24h_usd: float

    @property
    def seconds_to_funding(self) -> int:
        now_ms = int(time.time() * 1000)
        return max(0, (self.next_funding_ts - now_ms) // 1000)


class BybitClient:
    taker_fee = 0.00055

    def __init__(self, api_key="", api_secret="", testnet=False):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base = "https://api-testnet.bybit.com" if testnet else "https://api.bybit.com"
        self._session: Optional[aiohttp.ClientSession] = None

    async def _sess(self):
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def get_all_tickers(self) -> list[Ticker]:
        """Все linear перпы с funding rate и временем до выплаты."""
        session = await self._sess()
        try:
            async with session.get(
                f"{self.base}/v5/market/tickers",
                params={"category": "linear"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                data = await r.json()
        except Exception as e:
            logger.error(f"get_all_tickers failed: {e}")
            return []

        result = []
        for t in data.get("result", {}).get("list", []):
            sym = t.get("symbol", "")
            if not sym.endswith("USDT"):
                continue
            try:
                result.append(Ticker(
                    symbol=sym,
                    coin=sym[:-4],
                    funding_rate_pct=float(t["fundingRate"]) * 100,
                    next_funding_ts=int(t["nextFundingTime"]),
                    mark_price=float(t["markPrice"]),
                    volume_24h_usd=float(t.get("turnover24h", 0)),
                ))
            except (KeyError, ValueError):
                continue
        return result

    async def get_mark_price(self, symbol: str) -> Optional[float]:
        """Текущий mark price одной монеты."""
        session = await self._sess()
        try:
            async with session.get(
                f"{self.base}/v5/market/tickers",
                params={"category": "linear", "symbol": symbol},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as r:
                data = await r.json()
            items = data.get("result", {}).get("list", [])
            return float(items[0]["markPrice"]) if items else None
        except Exception as e:
            logger.error(f"get_mark_price({symbol}) failed: {e}")
            return None
