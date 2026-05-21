"""Telegram уведомления с подробной информацией по каждой позиции."""
from datetime import datetime, timezone, timedelta
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import aiohttp
from loguru import logger


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, tz: str = "UTC"):
        self.token = token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{token}"
        self._session: Optional[aiohttp.ClientSession] = None
        self.enabled = bool(token and chat_id)
        if not self.enabled:
            logger.warning("Telegram отключен — token или chat_id не заданы")
        try:
            self._tz = ZoneInfo(tz)
        except ZoneInfoNotFoundError:
            logger.warning(f"Неизвестный timezone '{tz}', используем UTC")
            self._tz = ZoneInfo("UTC")

    def _now(self) -> datetime:
        return datetime.now(self._tz)

    def _fmt_time(self, dt: datetime) -> str:
        """Форматирует время в локальной зоне: 14:35"""
        return dt.astimezone(self._tz).strftime("%H:%M")

    def _fmt_dt(self, dt: datetime) -> str:
        """Форматирует дату+время: 21 мая 14:35"""
        months = ["янв","фев","мар","апр","май","июн","июл","авг","сен","окт","ноя","дек"]
        local = dt.astimezone(self._tz)
        return f"{local.day} {months[local.month-1]} {local.strftime('%H:%M')}"

    def _until(self, ts_ms: int) -> str:
        """Время до события: 'через 2ч 15м' или 'через 45м'"""
        if not ts_ms:
            return ""
        now_ms = datetime.now(timezone.utc).timestamp() * 1000
        diff_sec = max(0, (ts_ms - now_ms) / 1000)
        h = int(diff_sec // 3600)
        m = int((diff_sec % 3600) // 60)
        if h > 0:
            return f"через {h}ч {m}м"
        return f"через {m}м"

    def _next_funding_line(self, exchange: str, ts_ms: int, interval_hours: int) -> str:
        """Строка с временем следующего фандинга."""
        if ts_ms and ts_ms > 0:
            dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
            return f"  {exchange}: {self._fmt_time(dt)} [{self._until(ts_ms)}]"
        # Если ts нет (Hyperliquid) — следующий полный час
        now = datetime.now(timezone.utc)
        next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        ts_ms = int(next_hour.timestamp() * 1000)
        return f"  {exchange}: {self._fmt_time(next_hour)} [{self._until(ts_ms)}]"

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def send(self, text: str, parse_mode: str = "HTML") -> bool:
        if not self.enabled:
            logger.info(f"[TG disabled] {text[:80]}")
            return False
        session = await self._get_session()
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        try:
            async with session.post(
                f"{self.base_url}/sendMessage", json=payload, timeout=10
            ) as resp:
                data = await resp.json()
                if not data.get("ok"):
                    logger.error(f"Telegram error: {data}")
                    return False
                return True
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")
            return False

    # ── Уведомления ────────────────────────────────────────────────────────────

    async def opened(
        self,
        op,
        size_usd: float,
        entry_short_price: float,
        entry_long_price: float,
        fees_usd: float,
        short_next_ts: int = 0,
        long_next_ts: int = 0,
    ) -> None:
        profit_per_8h_usd = size_usd * op.profit_per_8h_pct / 100
        profit_per_day_pct = op.profit_per_8h_pct * 3
        profit_per_day_usd = profit_per_8h_usd * 3

        short_line = self._next_funding_line(op.short_exchange, short_next_ts, 8)
        long_line = self._next_funding_line(op.long_exchange, long_next_ts, 1)

        text = (
            f"📈 <b>ОТКРЫТА ПОЗИЦИЯ — {op.symbol}</b>\n"
            f"🕐 {self._fmt_dt(self._now())}\n"
            f"\n"
            f"📉 SHORT: <b>{op.short_exchange}</b>  {op.short_rate_8h:+.4f}% / 8h\n"
            f"📈 LONG:  <b>{op.long_exchange}</b>  {op.long_rate_8h:+.4f}% / 8h\n"
            f"\n"
            f"💰 <b>Ожидаемый доход:</b>\n"
            f"  За 8h:   {op.profit_per_8h_pct:+.3f}% → <b>+${profit_per_8h_usd:.2f}</b>\n"
            f"  В сутки: {profit_per_day_pct:+.3f}% → <b>+${profit_per_day_usd:.2f}</b>\n"
            f"  APR: ~<b>{op.annual_pct:.0f}%</b>\n"
            f"\n"
            f"📊 Размер позиции: <b>${size_usd:,.2f}</b>\n"
            f"💸 Комиссии на вход: <b>-${fees_usd:.3f}</b>\n"
            f"🔖 Цена входа: SHORT ${entry_short_price:.4f} | LONG ${entry_long_price:.4f}\n"
            f"📐 Basis: {op.basis_pct:.3f}%\n"
            f"\n"
            f"⏰ <b>Следующий funding:</b>\n"
            f"{short_line}\n"
            f"{long_line}"
        )
        await self.send(text)

    async def funding_paid(
        self,
        symbol: str,
        exchange: str,
        amount_usd: float,
        total_funding_usd: float,
        next_ts: int = 0,
        interval_hours: int = 8,
    ) -> None:
        sign = "+" if amount_usd >= 0 else ""
        emoji = "💚" if amount_usd >= 0 else "🔴"
        next_line = self._next_funding_line(exchange, next_ts, interval_hours)

        text = (
            f"{emoji} <b>FUNDING — {symbol}</b>\n"
            f"🕐 {self._fmt_dt(self._now())}\n"
            f"\n"
            f"🏦 {exchange}: <b>{sign}${amount_usd:.4f}</b>\n"
            f"\n"
            f"📊 Накоплено по позиции: <b>${total_funding_usd:+.4f}</b>\n"
            f"⏰ Следующая выплата:\n"
            f"{next_line}"
        )
        await self.send(text)

    async def closed(
        self,
        symbol: str,
        reason: str,
        total_pnl: float,
        funding_received: float,
        price_pnl: float,
        fees: float,
        opened_at: datetime,
    ) -> None:
        emoji = "✅" if total_pnl >= 0 else "❌"
        pct = total_pnl  # будет передан как абсолютный $, pct считаем отдельно

        # Продолжительность позиции
        duration = datetime.now(timezone.utc) - opened_at.replace(tzinfo=timezone.utc)
        total_min = int(duration.total_seconds() // 60)
        hours, minutes = divmod(total_min, 60)
        duration_str = f"{hours}ч {minutes}м" if hours else f"{minutes}м"

        reason_map = {
            "stop_loss":      "🛑 Стоп-лосс",
            "funding_flipped": "🔄 Funding перевернулся",
            "funding_low":    "📉 Funding упал ниже порога",
            "basis_wide":     "📐 Basis расширился",
            "manual":         "👤 Закрыто вручную",
        }
        reason_str = reason_map.get(reason, reason)

        text = (
            f"{emoji} <b>ПОЗИЦИЯ ЗАКРЫТА — {symbol}</b>\n"
            f"🕐 {self._fmt_dt(self._now())}\n"
            f"\n"
            f"📋 Причина: {reason_str}\n"
            f"⏱ Держали: <b>{duration_str}</b>\n"
            f"\n"
            f"💰 <b>Разбивка PnL:</b>\n"
            f"  Funding:      <b>+${funding_received:.4f}</b>\n"
            f"  Price PnL:    <b>${price_pnl:+.4f}</b>\n"
            f"  Комиссии:     <b>-${fees:.4f}</b>\n"
            f"  {'─'*22}\n"
            f"  Итого: <b>${total_pnl:+.4f}</b>"
        )
        await self.send(text)

    async def started(self, mode: str, capital: float) -> None:
        await self.send(
            f"🤖 <b>Бот запущен</b>\n"
            f"🕐 {self._fmt_dt(self._now())}\n"
            f"Режим: <b>{mode.upper()}</b>\n"
            f"Капитал: <b>${capital:,.0f}</b>"
        )

    async def heartbeat(self, open_positions: list, day_pnl: float) -> None:
        lines = []
        for p in open_positions:
            pnl_emoji = "📈" if (p.get("pnl", 0) >= 0) else "📉"
            lines.append(
                f"  {pnl_emoji} {p['symbol']}: ${p['pnl']:+.2f} | {p['duration']}"
            )
        pos_text = "\n".join(lines) if lines else "  нет открытых позиций"
        day_emoji = "💚" if day_pnl >= 0 else "🔴"

        await self.send(
            f"💓 <b>Пульс</b> — {self._fmt_dt(self._now())}\n"
            f"\n"
            f"📊 Открыто: <b>{len(open_positions)}</b> позиций\n"
            f"{pos_text}\n"
            f"\n"
            f"{day_emoji} PnL сегодня: <b>${day_pnl:+.2f}</b>"
        )

    async def warning(self, msg: str) -> None:
        await self.send(f"⚠️ <b>ВНИМАНИЕ</b>\n{msg}")

    async def error(self, msg: str) -> None:
        await self.send(f"🚨 <b>ОШИБКА</b>\n{msg}")
