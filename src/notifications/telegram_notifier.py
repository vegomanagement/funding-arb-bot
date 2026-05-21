"""Telegram уведомления.

Используем прямой HTTP к Bot API чтобы не тянуть тяжёлую python-telegram-bot
библиотеку — нам нужна только отправка сообщений.
"""
from typing import Optional
import aiohttp
from loguru import logger


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{token}"
        self._session: Optional[aiohttp.ClientSession] = None
        self.enabled = bool(token and chat_id)
        if not self.enabled:
            logger.warning("Telegram отключен — token или chat_id не заданы")

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def send(self, text: str, parse_mode: str = "HTML") -> bool:
        """Отправить сообщение. Возвращает True если успешно."""
        if not self.enabled:
            logger.info(f"[TG disabled] {text}")
            return False

        session = await self._get_session()
        url = f"{self.base_url}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }

        try:
            async with session.post(url, json=payload, timeout=10) as resp:
                data = await resp.json()
                if not data.get("ok"):
                    logger.error(f"Telegram error: {data}")
                    return False
                return True
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")
            return False

    async def opportunity(self, op) -> None:
        text = (
            f"🟢 <b>Возможность</b> {op.symbol}\n"
            f"SHORT <b>{op.short_exchange}</b>: {op.short_rate_8h:+.4f}% / 8h\n"
            f"LONG <b>{op.long_exchange}</b>: {op.long_rate_8h:+.4f}% / 8h\n"
            f"Profit: <b>{op.profit_per_8h_pct:.4f}%</b> за 8h (~{op.annual_pct:.0f}% APR)\n"
            f"Basis: {op.basis_pct:.2f}%, Vol24h: ${op.min_volume_24h:,.0f}"
        )
        await self.send(text)

    async def opened(self, symbol: str, short_ex: str, long_ex: str,
                     size_usd: float, expected_profit_8h_pct: float) -> None:
        text = (
            f"📈 <b>Открыта позиция</b> {symbol}\n"
            f"SHORT {short_ex} | LONG {long_ex}\n"
            f"Размер: ${size_usd:.2f}\n"
            f"Ожидаемый profit: {expected_profit_8h_pct:.4f}% / 8h"
        )
        await self.send(text)

    async def funding_paid(self, symbol: str, exchange: str, amount: float) -> None:
        sign = "+" if amount >= 0 else ""
        text = (
            f"💰 <b>Funding</b> {symbol} на {exchange}\n"
            f"{sign}${amount:.4f}"
        )
        await self.send(text)

    async def closed(self, symbol: str, reason: str, total_pnl: float,
                     funding_received: float, fees: float) -> None:
        emoji = "✅" if total_pnl >= 0 else "❌"
        text = (
            f"{emoji} <b>Закрыта</b> {symbol}\n"
            f"Причина: {reason}\n"
            f"Funding: +${funding_received:.4f}\n"
            f"Комиссии: -${fees:.4f}\n"
            f"<b>PnL: ${total_pnl:+.4f}</b>"
        )
        await self.send(text)

    async def warning(self, msg: str) -> None:
        await self.send(f"⚠️ <b>WARNING</b>\n{msg}")

    async def error(self, msg: str) -> None:
        await self.send(f"🚨 <b>ERROR</b>\n{msg}")

    async def daily_report(self, stats: dict) -> None:
        text = (
            f"📊 <b>Дневной отчёт</b>\n"
            f"Сделок: {stats.get('trades', 0)}\n"
            f"Win rate: {stats.get('win_rate', 0):.0%}\n"
            f"Funding получено: ${stats.get('funding_total', 0):.2f}\n"
            f"Комиссии: ${stats.get('fees_total', 0):.2f}\n"
            f"<b>PnL за день: ${stats.get('pnl', 0):+.2f}</b>"
        )
        await self.send(text)
