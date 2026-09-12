from __future__ import annotations

import datetime as dt

import pytest

from app.config import Settings
from app.db.models import SignalDirection
from app.risk.engine import OpenPosition, RiskEngine, RiskState
from app.strategy.signals import TradeSignal


def _settings(**overrides) -> Settings:
    defaults = dict(
        total_capital_inr=5000.0,
        protected_capital_inr=4000.0,
        active_trading_capital_inr=1000.0,
        risk_per_trade_pct=2.0,
        daily_loss_ceiling_inr=50.0,
        weekly_loss_ceiling_inr=150.0,
        max_open_positions=2,
        max_trades_per_day=4,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _long_signal(symbol: str = "X", confidence: float = 0.9, entry=100.0, stop=95.0, target=115.0) -> TradeSignal:
    return TradeSignal(
        symbol=symbol,
        timestamp=dt.datetime(2024, 1, 1),
        direction=SignalDirection.LONG,
        entry_price=entry,
        stop_loss=stop,
        target_price=target,
        confidence=confidence,
    )


def test_position_sizing_uses_configured_risk_percent_of_active_capital():
    engine = RiskEngine(_settings(risk_per_trade_pct=2.0))
    signal = _long_signal(entry=100.0, stop=95.0)  # risk/share = 5
    state = RiskState(active_trading_capital_inr=1000.0)
    decision = engine.evaluate(signal, state)
    assert decision.approved
    # risk budget = 1000 * 2% = 20; qty = floor(20/5) = 4
    assert decision.quantity == 4
    assert decision.risk_amount_inr == 20.0


def test_no_leverage_caps_quantity_to_available_cash():
    engine = RiskEngine(_settings(risk_per_trade_pct=50.0))  # exaggerate so cash cap binds first
    signal = _long_signal(entry=100.0, stop=99.0)  # risk/share = 1 -> qty_by_risk would be 500
    state = RiskState(active_trading_capital_inr=1000.0)
    decision = engine.evaluate(signal, state)
    assert decision.approved
    assert decision.quantity == 10  # floor(1000 / 100)


def test_tiny_capital_rejects_when_size_rounds_to_zero_shares():
    engine = RiskEngine(_settings(risk_per_trade_pct=1.5))
    signal = _long_signal(entry=100.0, stop=50.0)  # huge stop distance
    state = RiskState(active_trading_capital_inr=10.0)
    decision = engine.evaluate(signal, state)
    assert not decision.approved
    assert "too small" in decision.reason


def test_missing_or_equal_stop_loss_is_rejected():
    engine = RiskEngine(_settings())
    signal = _long_signal(entry=100.0, stop=100.0)
    decision = engine.evaluate(signal, RiskState(active_trading_capital_inr=1000.0))
    assert not decision.approved
    assert "stop-loss" in decision.reason


def test_stop_on_wrong_side_of_entry_is_rejected_for_long():
    engine = RiskEngine(_settings())
    signal = _long_signal(entry=100.0, stop=105.0)  # stop above entry for a LONG
    decision = engine.evaluate(signal, RiskState(active_trading_capital_inr=1000.0))
    assert not decision.approved


def test_daily_loss_ceiling_blocks_new_trades_regardless_of_confidence():
    engine = RiskEngine(_settings(daily_loss_ceiling_inr=50.0))
    signal = _long_signal(confidence=0.99)
    state = RiskState(active_trading_capital_inr=1000.0, realized_pnl_today_inr=-60.0)
    decision = engine.evaluate(signal, state)
    assert not decision.approved
    assert "daily loss ceiling" in decision.reason


def test_weekly_drawdown_ceiling_blocks_new_trades():
    engine = RiskEngine(_settings(weekly_loss_ceiling_inr=150.0))
    signal = _long_signal()
    state = RiskState(active_trading_capital_inr=1000.0, realized_pnl_this_week_inr=-200.0)
    decision = engine.evaluate(signal, state)
    assert not decision.approved
    assert "weekly drawdown ceiling" in decision.reason


def test_max_open_positions_blocks_a_new_trade():
    engine = RiskEngine(_settings(max_open_positions=1))
    signal = _long_signal(symbol="Y")
    state = RiskState(
        active_trading_capital_inr=1000.0,
        open_positions=[OpenPosition(symbol="X", direction=SignalDirection.LONG)],
    )
    decision = engine.evaluate(signal, state)
    assert not decision.approved
    assert "max open positions" in decision.reason


def test_max_trades_per_day_blocks_a_new_trade():
    engine = RiskEngine(_settings(max_trades_per_day=1))
    signal = _long_signal()
    state = RiskState(active_trading_capital_inr=1000.0, trades_taken_today=1)
    decision = engine.evaluate(signal, state)
    assert not decision.approved
    assert "max trades per day" in decision.reason


def test_no_averaging_down_rejects_a_second_position_in_same_symbol():
    engine = RiskEngine(_settings())
    signal = _long_signal(symbol="X")
    state = RiskState(
        active_trading_capital_inr=1000.0,
        open_positions=[OpenPosition(symbol="X", direction=SignalDirection.LONG)],
    )
    decision = engine.evaluate(signal, state)
    assert not decision.approved
    assert "averaging down" in decision.reason


def test_no_trade_signal_is_never_approved():
    engine = RiskEngine(_settings())
    signal = TradeSignal(
        symbol="X",
        timestamp=dt.datetime(2024, 1, 1),
        direction=SignalDirection.NO_TRADE,
        entry_price=0.0,
        stop_loss=0.0,
        target_price=0.0,
        confidence=0.0,
    )
    decision = engine.evaluate(signal, RiskState(active_trading_capital_inr=1000.0))
    assert not decision.approved
