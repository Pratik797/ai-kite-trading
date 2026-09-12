"""
SQLAlchemy models. SQLite for development (per spec section 3); the same
models work unchanged against PostgreSQL in production by just changing
DATABASE_URL.

Tables map onto spec modules:
- CapitalLedgerEntry -> Profit Locker / capital-floor accounting
- Signal              -> Trade Signals module
- Trade               -> Portfolio module (paper trades in this pass; live
                          fills use the same table once the broker adapter exists)
- BacktestRun         -> Backtesting module (versioned, reproducible runs)
- JournalEntry        -> Trade Journal module (append-only, never updated/deleted)
"""
from __future__ import annotations

import datetime as dt
import enum

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class SignalDirection(str, enum.Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NO_TRADE = "NO_TRADE"


class TradeStatus(str, enum.Enum):
    OPEN = "OPEN"
    CLOSED_TARGET = "CLOSED_TARGET"
    CLOSED_STOP = "CLOSED_STOP"
    CLOSED_MANUAL = "CLOSED_MANUAL"
    CLOSED_EOD = "CLOSED_EOD"          # intraday square-off


class CapitalLedgerEntry(Base):
    """Immutable ledger of every change to protected / active / locked capital.
    Never overwritten — a new row is appended for every change so the full
    history of the capital split is auditable (spec section 11: audit trail)."""

    __tablename__ = "capital_ledger"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    protected_capital_inr: Mapped[float] = mapped_column(Float)
    active_trading_capital_inr: Mapped[float] = mapped_column(Float)
    locked_profit_inr: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(String(255))  # e.g. "initial_split", "profit_lock_cycle", "manual_adjustment"


class Signal(Base):
    """A candidate trade signal produced by the strategy layer, before the
    risk engine has approved/rejected it. Mirrors spec's Trade Signals module
    fields: entry/stop/target, position size, risk/reward, confidence, expiry,
    approval state."""

    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    symbol: Mapped[str] = mapped_column(String(32))
    direction: Mapped[SignalDirection] = mapped_column(Enum(SignalDirection))
    entry_price: Mapped[float] = mapped_column(Float)
    stop_loss: Mapped[float] = mapped_column(Float)
    target_price: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)          # 0-1, deterministic-strategy confidence for now
    reasons: Mapped[str] = mapped_column(Text)                 # JSON-encoded list of strings
    risk_reward_ratio: Mapped[float] = mapped_column(Float)
    strategy_name: Mapped[str] = mapped_column(String(64))     # e.g. "baseline_ema_rsi_atr"
    approved: Mapped[bool] = mapped_column(default=False)
    rejection_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    trades: Mapped[list["Trade"]] = relationship(back_populates="signal")


class Trade(Base):
    """A position taken (paper or live) off the back of an approved signal,
    sized by the risk engine."""

    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("signals.id"), nullable=True)
    backtest_run_id: Mapped[int | None] = mapped_column(ForeignKey("backtest_runs.id"), nullable=True)

    symbol: Mapped[str] = mapped_column(String(32))
    direction: Mapped[SignalDirection] = mapped_column(Enum(SignalDirection))
    entry_time: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    entry_price: Mapped[float] = mapped_column(Float)
    quantity: Mapped[int] = mapped_column(Integer)
    stop_loss: Mapped[float] = mapped_column(Float)
    target_price: Mapped[float] = mapped_column(Float)
    risk_amount_inr: Mapped[float] = mapped_column(Float)       # rupee risk at entry (for expectancy calc)

    exit_time: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[TradeStatus] = mapped_column(Enum(TradeStatus), default=TradeStatus.OPEN)

    gross_pnl_inr: Mapped[float | None] = mapped_column(Float, nullable=True)
    costs_inr: Mapped[float | None] = mapped_column(Float, nullable=True)   # brokerage + STT + slippage
    net_pnl_inr: Mapped[float | None] = mapped_column(Float, nullable=True)

    is_paper: Mapped[bool] = mapped_column(default=True)   # always True until a live broker adapter exists

    signal: Mapped[Signal | None] = relationship(back_populates="trades")


class BacktestRun(Base):
    """One reproducible backtest execution with versioned parameters, per
    spec section 8/14 (reproducible backtests with versioned parameters)."""

    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    strategy_name: Mapped[str] = mapped_column(String(64))
    symbols: Mapped[str] = mapped_column(Text)          # JSON-encoded list
    start_date: Mapped[str] = mapped_column(String(16))
    end_date: Mapped[str] = mapped_column(String(16))
    is_out_of_sample: Mapped[bool] = mapped_column(default=False)
    parameters_json: Mapped[str] = mapped_column(Text)   # full config snapshot, for reproducibility
    metrics_json: Mapped[str] = mapped_column(Text)       # computed metrics, see backtest/metrics.py


class JournalEntry(Base):
    """Append-only. Application code must never UPDATE or DELETE a row here —
    only INSERT. This is the audit trail spec section 11 and the Trade Journal
    module require."""

    __tablename__ = "journal_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    event_type: Mapped[str] = mapped_column(String(64))   # "signal_generated", "signal_rejected", "trade_opened", ...
    payload_json: Mapped[str] = mapped_column(Text)        # full structured record of the event
