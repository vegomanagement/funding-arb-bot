"""Тесты риск-менеджера."""
import pytest
from datetime import datetime, timedelta

from src.database.db import get_session
from src.database.models import Position
from src.strategy.risk_manager import RiskManager


def make_risk(**kwargs):
    defaults = dict(
        capital_usd=1000,
        max_positions=3,
        max_position_pct=30,
        stop_loss_pct=2,
        daily_drawdown_limit_pct=5,
        max_basis_pct=0.5,
    )
    defaults.update(kwargs)
    return RiskManager(**defaults)


def open_position(symbol="BTC", mode="paper", size_usd=300.0, total_pnl=0.0):
    with get_session() as s:
        pos = Position(
            symbol=symbol, mode=mode, status="open",
            opened_at=datetime.utcnow(),
            long_exchange="hyperliquid", short_exchange="bybit",
            size_usd=size_usd, size_coin=size_usd / 100,
            entry_long_price=100.0, entry_short_price=100.0,
            entry_funding_diff_pct=0.05,
            total_pnl_usd=total_pnl,
        )
        s.add(pos)
        s.flush()
        return pos.id


def close_position(pos_id, total_pnl):
    with get_session() as s:
        pos = s.get(Position, pos_id)
        pos.status = "closed"
        pos.closed_at = datetime.utcnow()
        pos.total_pnl_usd = total_pnl


# ── can_open ────────────────────────────────────────────────────────────────

def test_can_open_with_no_positions():
    ok, reason = make_risk().can_open(mode="paper", symbol="BTC", basis_pct=0.1)
    assert ok
    assert reason == ""


def test_blocks_when_max_positions_reached():
    risk = make_risk(max_positions=2)
    open_position("BTC")
    open_position("ETH")
    ok, reason = risk.can_open(mode="paper", symbol="SOL", basis_pct=0.1)
    assert not ok
    assert "2" in reason


def test_blocks_duplicate_symbol():
    open_position("BTC")
    ok, reason = make_risk().can_open(mode="paper", symbol="BTC", basis_pct=0.1)
    assert not ok
    assert "BTC" in reason


def test_blocks_high_basis():
    ok, reason = make_risk(max_basis_pct=0.5).can_open(
        mode="paper", symbol="BTC", basis_pct=0.8
    )
    assert not ok
    assert "basis" in reason


def test_blocks_daily_drawdown():
    risk = make_risk(capital_usd=1000, daily_drawdown_limit_pct=5)  # лимит -$50
    pid = open_position("BTC", total_pnl=0)
    close_position(pid, total_pnl=-60)  # убыток $60 > лимита $50
    ok, reason = risk.can_open(mode="paper", symbol="ETH", basis_pct=0.1)
    assert not ok
    assert "drawdown" in reason.lower()


def test_allows_within_drawdown():
    risk = make_risk(capital_usd=1000, daily_drawdown_limit_pct=5)  # лимит -$50
    pid = open_position("BTC", total_pnl=0)
    close_position(pid, total_pnl=-30)  # убыток $30 < лимита
    ok, _ = risk.can_open(mode="paper", symbol="ETH", basis_pct=0.1)
    assert ok


def test_different_modes_dont_interfere():
    """Позиции в paper не блокируют testnet."""
    risk = make_risk(max_positions=1)
    open_position("BTC", mode="paper")
    ok, _ = risk.can_open(mode="testnet", symbol="BTC", basis_pct=0.1)
    assert ok


# ── position_size ────────────────────────────────────────────────────────────

def test_position_size():
    risk = make_risk(capital_usd=1000, max_position_pct=30)
    assert risk.position_size_usd() == 300.0


# ── should_stop_loss ─────────────────────────────────────────────────────────

def test_stop_loss_triggered():
    risk = make_risk(stop_loss_pct=2)
    pid = open_position("BTC", size_usd=1000, total_pnl=-25)  # -2.5% > 2%
    with get_session() as s:
        pos = s.get(Position, pid)
        assert risk.should_stop_loss(pos)


def test_stop_loss_not_triggered():
    risk = make_risk(stop_loss_pct=2)
    pid = open_position("BTC", size_usd=1000, total_pnl=-15)  # -1.5% < 2%
    with get_session() as s:
        pos = s.get(Position, pid)
        assert not risk.should_stop_loss(pos)
