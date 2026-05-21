"""Hyperliquid API адаптер.

Hyperliquid платит funding каждый ЧАС (а не каждые 8 часов как Bybit).
Это ключевая разница — при сравнении нужно нормализовать к одной шкале (8h).
"""
from typing import Optional
import aiohttp
from loguru import logger

from .base import ExchangeBase, FundingInfo, Orderbook


class HyperliquidClient(ExchangeBase):
    name = "hyperliquid"
    taker_fee = 0.00045            # 0.045% taker
    funding_interval_hours = 1     # выплаты каждый час

    def __init__(self, private_key: str = "", address: str = "", testnet: bool = False):
        self.private_key = private_key
        self.address = address
        self.base_url = (
            "https://api.hyperliquid-testnet.xyz" if testnet
            else "https://api.hyperliquid.xyz"
        )
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _post(self, endpoint: str, body: dict) -> Optional[dict]:
        session = await self._get_session()
        url = f"{self.base_url}{endpoint}"
        try:
            async with session.post(url, json=body, timeout=10) as resp:
                return await resp.json()
        except Exception as e:
            logger.error(f"Hyperliquid POST {endpoint} failed: {e}")
            return None

    async def get_all_funding_rates(self) -> list[FundingInfo]:
        """Получить funding по всем перпам.

        Hyperliquid возвращает funding rate за 1 час (доля).
        Конвертируем: rate_8h = rate_1h * 8 * 100 (в %).
        """
        data = await self._post("/info", {"type": "metaAndAssetCtxs"})
        if not data or not isinstance(data, list) or len(data) < 2:
            return []

        try:
            universe = data[0].get("universe", [])
            ctxs = data[1]
        except (KeyError, AttributeError):
            return []

        if len(universe) != len(ctxs):
            logger.warning(
                f"Hyperliquid: universe ({len(universe)}) и ctxs ({len(ctxs)}) "
                "разной длины"
            )
            return []

        result: list[FundingInfo] = []
        for meta, ctx in zip(universe, ctxs):
            symbol = meta.get("name", "")
            if not symbol:
                continue

            try:
                # funding в Hyperliquid это доля за 1 час
                rate_1h = float(ctx.get("funding", 0))
                rate_8h = rate_1h * 8 * 100  # в % за 8h
                mark = float(ctx.get("markPx", 0))
                # dayNtlVlm — оборот за 24h в USD
                volume = float(ctx.get("dayNtlVlm", 0))
                # premium можно использовать как proxy для predicted, но
                # пока используем current rate
                predicted_8h = rate_8h
            except (ValueError, TypeError):
                continue

            if mark <= 0:
                continue

            result.append(FundingInfo(
                symbol=symbol,
                exchange=self.name,
                current_rate_8h=rate_8h,
                predicted_rate_8h=predicted_8h,
                next_funding_ts=0,  # HL: следующий час, посчитаем при необходимости
                mark_price=mark,
                volume_24h_usd=volume,
            ))

        return result

    async def get_funding_rate(self, symbol: str) -> Optional[FundingInfo]:
        rates = await self.get_all_funding_rates()
        for r in rates:
            if r.symbol == symbol:
                return r
        return None

    async def get_orderbook(self, symbol: str) -> Optional[Orderbook]:
        data = await self._post("/info", {"type": "l2Book", "coin": symbol})
        if not data:
            return None

        levels = data.get("levels", [])
        if len(levels) < 2:
            return None

        bids, asks = levels[0], levels[1]
        if not bids or not asks:
            return None

        try:
            return Orderbook(
                symbol=symbol,
                exchange=self.name,
                best_bid=float(bids[0]["px"]),
                best_ask=float(asks[0]["px"]),
                bid_qty=float(bids[0]["sz"]),
                ask_qty=float(asks[0]["sz"]),
            )
        except (KeyError, ValueError, IndexError):
            return None

    async def get_balance(self) -> float:
        """Получить свободный USDC баланс через clearinghouseState."""
        if not self.address:
            return 0.0

        data = await self._post("/info", {
            "type": "clearinghouseState",
            "user": self.address,
        })
        if not data:
            return 0.0

        try:
            return float(data.get("withdrawable", 0))
        except (ValueError, TypeError):
            return 0.0
