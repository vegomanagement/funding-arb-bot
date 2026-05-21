"""Telegram бот команды для arb бота.

Polling loop запускается параллельно с основным ботом.
Команды: /stats /today /positions /help
"""
import asyncio
from datetime import datetime, date
from typing import Optional
import aiohttp
from loguru import logger

from src.database.db import get_session
from src.database.models import Position, FundingEvent, OpportunityLog


class TelegramCommands:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self.base = f"https://api.telegram.org/bot{token}"
        self.enabled = bool(token and chat_id)
        self._offset = 0
        self._session: Optional[aiohttp.ClientSession] = None

    async def _sess(self):
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def _send(self, text: str):
        if not self.enabled:
            return
        s = await self._sess()
        try:
            async with s.post(f"{self.base}/sendMessage", json={
                "chat_id": self.chat_id, "text": text,
                "parse_mode": "HTML", "disable_web_page_preview": True,
            }, timeout=aiohttp.ClientTimeout(total=10)) as r:
                await r.json()
        except Exception as e:
            logger.error(f"TG send error: {e}")

    async def _get_updates(self) -> list:
        s = await self._sess()
        try:
            async with s.get(f"{self.base}/getUpdates", params={
                "offset": self._offset, "timeout": 30, "allowed_updates": ["message"]
            }, timeout=aiohttp.ClientTimeout(total=35)) as r:
                data = await r.json()
                return data.get("result", [])
        except Exception:
            return []

    # ── Команды ────────────────────────────────────────────────

    def _cmd_help(self) -> str:
        return (
            "🤖 <b>Arb Bot команды</b>\n\n"
            "/stats      — вся статистика\n"
            "/today      — сделки за сегодня\n"
            "/positions  — открытые позиции\n"
            "/best       — топ-5 лучших сделок\n"
            "/help       — это сообщение"
        )

    def _cmd_stats(self, mode: str) -> str:
        with get_session() as s:
            all_closed = s.query(Position).filter_by(
                mode=mode, status="closed"
            ).all()
            open_pos = s.query(Position).filter_by(
                mode=mode, status="open"
            ).all()

        if not all_closed:
            return f"📊 Нет закрытых сделок в режиме <b>{mode}</b>"

        total = len(all_closed)
        wins = sum(1 for p in all_closed if (p.total_pnl_usd or 0) > 0)
        win_rate = wins / total * 100

        total_funding = sum(p.funding_received_usd or 0 for p in all_closed)
        total_fees = sum(p.fees_paid_usd or 0 for p in all_closed)
        total_pnl = sum(p.total_pnl_usd or 0 for p in all_closed)

        best = max(all_closed, key=lambda p: p.total_pnl_usd or 0)
        worst = min(all_closed, key=lambda p: p.total_pnl_usd or 0)

        return (
            f"📊 <b>Статистика Arb Bot</b> (mode: {mode})\n\n"
            f"Сделок закрыто:  <b>{total}</b>\n"
            f"Win rate:        <b>{win_rate:.0f}%</b>\n"
            f"Открытых:        <b>{len(open_pos)}</b>\n\n"
            f"Funding итого:   <b>+${total_funding:.2f}</b>\n"
            f"Комиссии:        <b>-${total_fees:.2f}</b>\n"
            f"───────────────────────\n"
            f"Чистый PnL:      <b>${total_pnl:+.2f}</b>\n\n"
            f"🏆 Лучшая: {best.symbol} ${best.total_pnl_usd:+.2f}\n"
            f"💀 Худшая:  {worst.symbol} ${worst.total_pnl_usd:+.2f}"
        )

    def _cmd_today(self, mode: str) -> str:
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        with get_session() as s:
            closed_today = s.query(Position).filter(
                Position.mode == mode,
                Position.status == "closed",
                Position.closed_at >= today,
            ).all()

        if not closed_today:
            return "📅 Сегодня закрытых сделок нет"

        pnl = sum(p.total_pnl_usd or 0 for p in closed_today)
        lines = [f"📅 <b>Сегодня</b> ({len(closed_today)} сделок, PnL: ${pnl:+.2f})\n"]
        for p in closed_today:
            emoji = "✅" if (p.total_pnl_usd or 0) >= 0 else "❌"
            lines.append(
                f"{emoji} {p.symbol} | ${p.total_pnl_usd:+.2f} | {p.close_reason}"
            )
        return "\n".join(lines)

    def _cmd_positions(self, mode: str) -> str:
        with get_session() as s:
            open_pos = s.query(Position).filter_by(
                mode=mode, status="open"
            ).all()

        if not open_pos:
            return "📭 Нет открытых позиций"

        lines = [f"📂 <b>Открытые позиции</b> ({len(open_pos)})\n"]
        for p in open_pos:
            age_h = (datetime.utcnow() - p.opened_at).total_seconds() / 3600
            lines.append(
                f"• {p.symbol}\n"
                f"  SHORT {p.short_exchange} | LONG {p.long_exchange}\n"
                f"  Размер: ${p.size_usd:.0f} | {age_h:.1f}ч\n"
                f"  Funding: +${p.funding_received_usd or 0:.2f} | PnL: ${p.total_pnl_usd or 0:+.2f}"
            )
        return "\n".join(lines)

    def _cmd_best(self, mode: str) -> str:
        with get_session() as s:
            closed = s.query(Position).filter_by(
                mode=mode, status="closed"
            ).order_by(Position.total_pnl_usd.desc()).limit(5).all()

        if not closed:
            return "Нет данных"

        lines = ["🏆 <b>Топ-5 сделок</b>\n"]
        for i, p in enumerate(closed, 1):
            lines.append(f"{i}. {p.symbol} ${p.total_pnl_usd:+.2f} ({p.close_reason})")
        return "\n".join(lines)

    # ── Polling loop ────────────────────────────────────────────

    async def run(self, mode: str):
        """Запустить polling loop. Вызывать как asyncio.create_task()."""
        if not self.enabled:
            logger.warning("Telegram команды отключены (нет token/chat_id)")
            return

        logger.info("Telegram command handler запущен")
        while True:
            try:
                updates = await self._get_updates()
                for update in updates:
                    self._offset = update["update_id"] + 1
                    msg = update.get("message", {})
                    text = msg.get("text", "").strip().split()[0].lower()

                    # Принимаем команды только от нашего chat_id
                    if str(msg.get("chat", {}).get("id")) != self.chat_id:
                        continue

                    if text == "/stats":
                        await self._send(self._cmd_stats(mode))
                    elif text == "/today":
                        await self._send(self._cmd_today(mode))
                    elif text == "/positions":
                        await self._send(self._cmd_positions(mode))
                    elif text == "/best":
                        await self._send(self._cmd_best(mode))
                    elif text in ("/help", "/start"):
                        await self._send(self._cmd_help())

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Telegram polling error: {e}")
                await asyncio.sleep(5)
