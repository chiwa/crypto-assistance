"""SQLAlchemy models for crypto-assistance modular monolith."""

from datetime import datetime
from sqlalchemy import BigInteger, Boolean, Column, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class SettingModel(Base):
    __tablename__ = "settings"
    key = Column(String(64), primary_key=True)
    value = Column(Text, nullable=False)


class LedgerModel(Base):
    __tablename__ = "ledger"
    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(String(64), nullable=False)
    mode = Column(String(16), nullable=False)
    type = Column(String(16), nullable=False)  # DEPOSIT, WITHDRAWAL, BUY, SELL, FEE, ADJUSTMENT
    pair = Column(String(16), nullable=True)
    asset = Column(String(16), nullable=True)
    quantity = Column(Float, nullable=False, default=0.0)
    amount_thb = Column(Float, nullable=False)
    fee_thb = Column(Float, nullable=False, default=0.0)
    price_thb = Column(Float, nullable=True)
    reference = Column(String(128), nullable=True)
    note = Column(Text, nullable=True)


class PositionModel(Base):
    __tablename__ = "positions"
    mode = Column(String(16), primary_key=True)
    asset = Column(String(16), primary_key=True)
    quantity = Column(Float, nullable=False, default=0.0)
    average_cost = Column(Float, nullable=False, default=0.0)
    realized_pnl = Column(Float, nullable=False, default=0.0)
    updated_at = Column(String(64), nullable=False)


class PositionPlanModel(Base):
    __tablename__ = "position_plans"
    mode = Column(String(16), primary_key=True)
    asset = Column(String(16), nullable=False)
    entry_time = Column(String(64), nullable=False)
    entry_price = Column(Float, nullable=False)
    strategy = Column(String(64), nullable=False)
    entry_score = Column(Integer, nullable=True)
    entry_reason = Column(Text, nullable=False)
    stop_loss = Column(Float, nullable=False)
    effective_stop = Column(Float, nullable=False)
    take_profit_1 = Column(Float, nullable=False)
    take_profit_2 = Column(Float, nullable=False)
    highest_price = Column(Float, nullable=False)
    trailing_enabled = Column(Integer, nullable=False, default=1)
    trailing_activation_percent = Column(Float, nullable=False, default=2.0)
    trailing_distance_percent = Column(Float, nullable=False, default=1.0)
    current_action = Column(String(32), nullable=False, default="IN_POSITION")
    action_reason = Column(Text, nullable=False, default="Position recorded")
    plan_type = Column(String(32), nullable=False, default="MARKET_DERIVED")
    updated_at = Column(String(64), nullable=False)


class PriceModel(Base):
    __tablename__ = "prices"
    pair = Column(String(16), primary_key=True)
    price = Column(Float, nullable=False)
    updated_at = Column(String(64), nullable=False)


class SignalModel(Base):
    __tablename__ = "signals"
    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(String(64), nullable=False)
    pair = Column(String(16), nullable=False)
    timeframe = Column(String(8), nullable=False)
    signal = Column(String(16), nullable=False)
    score = Column(Integer, nullable=False)
    regime = Column(String(32), nullable=False)
    details = Column(Text, nullable=False)


class JournalModel(Base):
    __tablename__ = "journal"
    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(String(64), nullable=False)
    mode = Column(String(16), nullable=False)
    title = Column(String(128), nullable=False)
    body = Column(Text, nullable=False)
    tags = Column(String(64), nullable=False, default="")


class NotificationModel(Base):
    __tablename__ = "notifications"
    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(String(64), nullable=False)
    level = Column(String(16), nullable=False)
    title = Column(String(128), nullable=False)
    message = Column(Text, nullable=False)
    is_read = Column(Integer, nullable=False, default=0)


class DailySnapshotModel(Base):
    __tablename__ = "daily_snapshots"
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(String(16), nullable=False)
    mode = Column(String(16), nullable=False)
    cash = Column(Float, nullable=False)
    position_value = Column(Float, nullable=False)
    equity = Column(Float, nullable=False)
    realized_pnl = Column(Float, nullable=False)
    unrealized_pnl = Column(Float, nullable=False)
    net_capital_inflow = Column(Float, nullable=False)
    created_at = Column(String(64), nullable=False)
    __table_args__ = (UniqueConstraint("date", "mode", name="uq_snapshot_date_mode"),)
