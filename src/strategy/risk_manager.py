"""Риск-менеджмент: лимиты, проверки перед открытием/закрытием."""
from datetime import datetime, timedelta
from loguru import logger

from src.database.db import get_session
from src.database.models import Position


class RiskManager:
    def __init__(
        self,
        capital_usd: float,
        max_positions: int = 3,
        max_position_pct: float = 30,
        stop_loss_pct: float = 2,
        daily_drawdown_limit_pct: float = 5,
        max_basis_pct: float = 0.5,
    ):
        self.capital = capital_usd
        self.max_positions = max_positions
        self.max_position_size = capital_usd * max_position_pct / 100
        self.stop_loss_pct = stop_loss_pct
        self.daily_drawdown_limit = capital_usd * daily_drawdown_limit_pct / 100
        self.max_basis_pct = max_basis_pct

    def position_size_usd(self) -> float:
        """Рекомендованный размер позиции."""
        return self.max_position_size

    def can_open(self, mode: str, symbol: str, basis_pct: float) -> tuple[bool, str]:
        """Можно ли открыть новую позицию.

        Возвращает (можно, причина если нельзя).
        """
        if basis_pct > self.max_basis_pct:
            return False, f"basis {basis_pct:.2f}% > лимита {self.max_basis_pct}%"

        with get_session() as s:
            open_positions = s.query(Position).filter_by(
                mode=mode, status="open"
            ).all()

            if len(open_positions) >= self.max_positions:
                return False, f"уже открыто {len(open_positions)} позиций (лимит {self.max_positions})"

            for p in open_positions:
                if p.symbol == symbol:
                    return False, f"{symbol} уже в позиции"

            # Дневной drawdown
            today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            closed_today = s.query(Position).filter(
                Position.mode == mode,
                Position.status == "closed",
                Position.closed_at >= today_start,
            ).all()
            day_pnl = sum(p.total_pnl_usd or 0 for p in closed_today)
            if day_pnl < -self.daily_drawdown_limit:
                return False, (
                    f"дневной drawdown ${day_pnl:.2f} превысил лимит "
                    f"-${self.daily_drawdown_limit:.2f}"
                )

        return True, ""

    def should_stop_loss(self, position: Position) -> bool:
        """Сработал ли stop-loss на позиции."""
        loss_limit = -position.size_usd * self.stop_loss_pct / 100
        return (position.total_pnl_usd or 0) < loss_limit
