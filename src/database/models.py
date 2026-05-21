"""SQLAlchemy модели."""
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, ForeignKey
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Position(Base):
    __tablename__ = "positions"

    id = Column(Integer, primary_key=True)
    symbol = Column(String, nullable=False, index=True)
    mode = Column(String, nullable=False)              # paper | testnet | live
    status = Column(String, nullable=False, default="open")  # open | closed

    opened_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    closed_at = Column(DateTime, nullable=True)

    long_exchange = Column(String, nullable=False)
    short_exchange = Column(String, nullable=False)

    size_usd = Column(Float, nullable=False)
    size_coin = Column(Float, nullable=False)

    entry_long_price = Column(Float, nullable=False)
    entry_short_price = Column(Float, nullable=False)
    exit_long_price = Column(Float, nullable=True)
    exit_short_price = Column(Float, nullable=True)

    entry_funding_diff_pct = Column(Float, nullable=False)

    funding_received_usd = Column(Float, default=0.0)
    fees_paid_usd = Column(Float, default=0.0)
    price_pnl_usd = Column(Float, default=0.0)
    total_pnl_usd = Column(Float, default=0.0)

    close_reason = Column(String, nullable=True)

    funding_events = relationship("FundingEvent", back_populates="position")


class FundingEvent(Base):
    __tablename__ = "funding_events"

    id = Column(Integer, primary_key=True)
    position_id = Column(Integer, ForeignKey("positions.id"), nullable=False)
    exchange = Column(String, nullable=False)
    paid_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    amount_usd = Column(Float, nullable=False)           # положительное = получили
    rate_pct = Column(Float, nullable=False)             # ставка за период
    period_hours = Column(Integer, nullable=False)        # 8 для Bybit, 1 для HL

    position = relationship("Position", back_populates="funding_events")


class BotSetting(Base):
    """Персистентные настройки бота (key-value)."""
    __tablename__ = "bot_settings"
    key = Column(String, primary_key=True)
    value = Column(String, nullable=False)


class OpportunityLog(Base):
    """Лог найденных возможностей для анализа."""
    __tablename__ = "opportunities"

    id = Column(Integer, primary_key=True)
    found_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    symbol = Column(String, nullable=False, index=True)
    short_exchange = Column(String, nullable=False)
    short_rate_8h = Column(Float, nullable=False)
    long_exchange = Column(String, nullable=False)
    long_rate_8h = Column(Float, nullable=False)
    profit_per_8h_pct = Column(Float, nullable=False)
    basis_pct = Column(Float, nullable=False)
    min_volume_24h = Column(Float, nullable=False)
    taken = Column(Boolean, default=False)
