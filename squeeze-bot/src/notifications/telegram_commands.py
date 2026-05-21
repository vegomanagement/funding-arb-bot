"""Telegram команды для squeeze бота.

Команды: /stats /today /trades /best /help
"""
import asyncio
from datetime import datetime
from typing import Optional
import aiohttp
from loguru import logger

from src.database.db import get_session
from src.database.models import SqueezeTrade


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
                "offset": self._offset, "timeout": 30,
                "allowed_updates": ["message"],
            }, timeout=aiohttp.ClientTimeout(total=35)) as r:
                data = await r.json()
                return data.get("result", [])
        except Exception:
            return []

    def _cmd_help(self) -> str:
        return (
            "⚡️ <b>Squeeze Bot команды</b>\n\n"
            "/stats   — общая статистика\n"
            "/today   — сделки за сегодня\n"
            "/trades  — последние 10 сделок\n"
            "/best    — топ-5 лучших\n"
            "/help    — это сообщение"
        )

    def _cmd_stats(self, mode: str) -> str:
        with get_session() as s:
            closed = s.query(SqueezeTrade).filter(
                SqueezeTrade.mode == mode,
                SqueezeTrade.closed_at.isnot(None),
            ).all()
            open_trades = s.query(SqueezeTrade).filter(
                SqueezeTrade.mode == mode,
                SqueezeTrade.closed_at.is_(None),
            ).all()

        if not closed:
            return f"📊 Нет закрытых сделок (mode: {mode})"

        total = len(closed)
        wins = sum(1 for t in closed if (t.total_pnl_usd or 0) > 0)
        win_rate = wins / total * 100

        by_reason = {}
        for t in closed:
            r = t.close_reason or "?"
            by_reason[r] = by_reason.get(r, 0) + 1

        total_funding = sum(t.funding_received_usd or 0 for t in closed)
        total_pnl = sum(t.total_pnl_usd or 0 for t in closed)
        total_fees = sum(t.fees_usd or 0 for t in closed)

        best = max(closed, key=lambda t: t.total_pnl_usd or 0)
        worst = min(closed, key=lambda t: t.total_pnl_usd or 0)

        reasons_str = " | ".join(f"{k}:{v}" for k, v in by_reason.items())

        return (
            f"⚡️ <b>Squeeze Bot</b> (mode: {mode})\n\n"
            f"Сделок:     <b>{total}</b> (открытых: {len(open_trades)})\n"
            f"Win rate:   <b>{win_rate:.0f}%</b> ({wins}W / {total-wins}L)\n"
            f"Закрыто по: {reasons_str}\n\n"
            f"Funding:    <b>+${total_funding:.2f}</b>\n"
            f"Комиссии:   <b>-${total_fees:.2f}</b>\n"
            f"──────────────────────\n"
            f"PnL итого:  <b>${total_pnl:+.2f}</b>\n\n"
            f"🏆 Лучшая: {best.symbol} ${best.total_pnl_usd:+.2f}\n"
            f"💀 Худшая:  {worst.symbol} ${worst.total_pnl_usd:+.2f}"
        )

    def _cmd_today(self, mode: str) -> str:
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        with get_session() as s:
            closed = s.query(SqueezeTrade).filter(
                SqueezeTrade.mode == mode,
                SqueezeTrade.closed_at >= today,
                SqueezeTrade.closed_at.isnot(None),
            ).order_by(SqueezeTrade.closed_at.desc()).all()

        if not closed:
            return "📅 Сегодня сделок нет"

        pnl = sum(t.total_pnl_usd or 0 for t in closed)
        lines = [f"📅 <b>Сегодня</b> ({len(closed)} сделок | PnL: ${pnl:+.2f})\n"]
        for t in closed:
            emoji = "✅" if (t.total_pnl_usd or 0) >= 0 else "❌"
            side = "L" if t.side == "Buy" else "S"
            lines.append(
                f"{emoji} {t.symbol} [{side}] ${t.total_pnl_usd:+.4f} | {t.close_reason}"
            )
        return "\n".join(lines)

    def _cmd_trades(self, mode: str) -> str:
        with get_session() as s:
            trades = s.query(SqueezeTrade).filter(
                SqueezeTrade.mode == mode,
                SqueezeTrade.closed_at.isnot(None),
            ).order_by(SqueezeTrade.closed_at.desc()).limit(10).all()

        if not trades:
            return "Нет закрытых сделок"

        lines = ["📋 <b>Последние 10 сделок</b>\n"]
        for t in trades:
            emoji = "✅" if (t.total_pnl_usd or 0) >= 0 else "❌"
            side = "ЛОНГ" if t.side == "Buy" else "ШОРТ"
            lines.append(
                f"{emoji} {t.symbol} {side} | ${t.total_pnl_usd:+.4f} | {t.close_reason}"
            )
        return "\n".join(lines)

    def _cmd_best(self, mode: str) -> str:
        with get_session() as s:
            trades = s.query(SqueezeTrade).filter(
                SqueezeTrade.mode == mode,
                SqueezeTrade.closed_at.isnot(None),
            ).order_by(SqueezeTrade.total_pnl_usd.desc()).limit(5).all()

        if not trades:
            return "Нет данных"

        lines = ["🏆 <b>Топ-5 сделок</b>\n"]
        for i, t in enumerate(trades, 1):
            side = "ЛОНГ" if t.side == "Buy" else "ШОРТ"
            lines.append(
                f"{i}. {t.symbol} {side} "
                f"funding={t.entry_funding_pct:.3f}% "
                f"→ ${t.total_pnl_usd:+.4f} ({t.close_reason})"
            )
        return "\n".join(lines)

    async def run(self, mode: str):
        if not self.enabled:
            logger.warning("Telegram команды отключены")
            return

        logger.info("Telegram commands запущены")
        while True:
            try:
                updates = await self._get_updates()
                for update in updates:
                    self._offset = update["update_id"] + 1
                    msg = update.get("message", {})
                    text = msg.get("text", "").strip().split()[0].lower()

                    if str(msg.get("chat", {}).get("id")) != self.chat_id:
                        continue

                    if text == "/stats":
                        await self._send(self._cmd_stats(mode))
                    elif text == "/today":
                        await self._send(self._cmd_today(mode))
                    elif text == "/trades":
                        await self._send(self._cmd_trades(mode))
                    elif text == "/best":
                        await self._send(self._cmd_best(mode))
                    elif text in ("/help", "/start"):
                        await self._send(self._cmd_help())

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Commands polling error: {e}")
                await asyncio.sleep(5)
