from typing import Optional
import aiohttp
from loguru import logger


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str):
        self.url = f"https://api.telegram.org/bot{token}/sendMessage"
        self.chat_id = chat_id
        self.enabled = bool(token and chat_id)
        self._session: Optional[aiohttp.ClientSession] = None
        if not self.enabled:
            logger.warning("Telegram отключен")

    async def _sess(self):
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def send(self, text: str):
        if not self.enabled:
            logger.info(f"[TG] {text}")
            return
        try:
            s = await self._sess()
            async with s.post(self.url, json={
                "chat_id": self.chat_id, "text": text,
                "parse_mode": "HTML", "disable_web_page_preview": True,
            }, timeout=aiohttp.ClientTimeout(total=10)) as r:
                data = await r.json()
                if not data.get("ok"):
                    logger.error(f"TG error: {data}")
        except Exception as e:
            logger.error(f"TG send failed: {e}")

    async def signal_found(self, sym, side, funding_pct, secs_left, mark):
        await self.send(
            f"⚡️ <b>СИГНАЛ</b> {sym}\n"
            f"Направление: <b>{'ЛОНГ' if side=='Buy' else 'ШОРТ'}</b>\n"
            f"Funding: <b>{funding_pct:+.4f}%</b> через {secs_left}с\n"
            f"Цена: ${mark}"
        )

    async def opened(self, sym, side, entry, size, funding_pct):
        await self.send(
            f"📈 <b>ВХОД</b> {sym} {'ЛОНГ' if side=='Buy' else 'ШОРТ'}\n"
            f"Цена входа: ${entry}\n"
            f"Размер: ${size:.2f}\n"
            f"Ожидаемый funding: {funding_pct:.4f}%"
        )

    async def closed(self, sym, reason, funding_usd, price_pnl, fees, total):
        emoji = "✅" if total >= 0 else "❌"
        reasons = {
            "tp": "Take Profit ✨",
            "sl": "Stop Loss 🛑",
            "timeout": "Таймаут ⏱",
            "funding_paid": "Funding получен 💰",
        }
        await self.send(
            f"{emoji} <b>ВЫХОД</b> {sym}\n"
            f"Причина: {reasons.get(reason, reason)}\n"
            f"Funding: +${funding_usd:.4f}\n"
            f"Цена PnL: ${price_pnl:+.4f}\n"
            f"Комиссии: -${fees:.4f}\n"
            f"<b>Итого: ${total:+.4f}</b>"
        )

    async def error(self, msg):
        await self.send(f"🚨 <b>ERROR</b>\n{msg}")
