from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class SqueezeTrade(Base):
    __tablename__ = "squeeze_trades"

    id = Column(Integer, primary_key=True)
    mode = Column(String, nullable=False)          # paper | live
    symbol = Column(String, nullable=False)
    side = Column(String, nullable=False)          # Buy | Sell

    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float, nullable=True)
    size_usd = Column(Float, nullable=False)

    entry_funding_pct = Column(Float, nullable=False)  # ожидаемый funding %
    funding_received_usd = Column(Float, default=0.0)  # фактически полученный funding
    price_pnl_usd = Column(Float, default=0.0)         # PnL от движения цены
    fees_usd = Column(Float, default=0.0)
    total_pnl_usd = Column(Float, nullable=True)

    close_reason = Column(String, nullable=True)  # tp | sl | timeout | funding_paid

    opened_at = Column(DateTime, default=datetime.utcnow)
    closed_at = Column(DateTime, nullable=True)
    funding_paid_at = Column(DateTime, nullable=True)
