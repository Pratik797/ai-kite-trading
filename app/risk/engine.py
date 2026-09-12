"""
Risk Manager (spec section 2 "Risk Manager" / section 6 Initial Risk Profile).

This is the ONE place a signal can become a real position. It independently
re-validates every numerical value on a TradeSignal — it does not trust the
strategy layer's confidence score, and it can reject a signal regardless of
how confident the strategy was (spec section 4: "A bullish/bearish AI answer
is never sufficient authorization to place an order" — the same rule applies
to the deterministic baseline strategy in this pass).

The engine is intentionally stateless per call: all "current state" (open
positions, today's/this week's realized P&L, trades taken today) is passed in
by the caller (the backtest engine in this pass; a live/paper trading loop in
a later phase) via `RiskState`. This keeps the engine trivially unit-testable
and keeps it from silently depending on hidden globals.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.config import Settings, settings as default_settings
from app.db.models import SignalDirection
from app.strategy.signals import TradeSignal


@dataclass
class OpenPosition:
    symbol: str
    direction: SignalDirection


@dataclass
class RiskState:
    """Everything the risk engine needs to know about the world right now,
    supplied by the caller. All P&L figures are realized (closed-trade) net
    P&L in rupees; unrealized P&L must never be passed in here (spec section
    7: unrealized P&L is never treated as protected/lockable cash, and it
    must not be allowed to mask a real daily/weekly loss limit breach)."""

    active_trading_capital_inr: float
    open_positions: list[OpenPosition] = field(default_factory=list)
    trades_taken_today: int = 0
    realized_pnl_today_inr: float = 0.0
    realized_pnl_this_week_inr: float = 0.0


@dataclass
class RiskDecision:
    approved: bool
    quantity: int = 0
    risk_amount_inr: float = 0.0
    reason: str = ""


class RiskEngine:
    """Deterministic, independent risk validation. Every check below can
    veto a trade on its own; the first failing check is the rejection
    reason (checks are ordered roughly by how fundamental the violation is:
    missing stop-loss and existing kill-switch conditions are checked before
    the more situational limits)."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or default_settings

    def evaluate(self, signal: TradeSignal, state: RiskState) -> RiskDecision:
        if signal.direction == SignalDirection.NO_TRADE:
            return RiskDecision(approved=False, reason="signal is NO_TRADE")

        if signal.stop_loss is None or signal.stop_loss == signal.entry_price:
            return RiskDecision(approved=False, reason="mandatory stop-loss missing or equal to entry")

        if signal.direction == SignalDirection.LONG and signal.stop_loss >= signal.entry_price:
            return RiskDecision(approved=False, reason="stop-loss is not below entry for a LONG")
        if signal.direction == SignalDirection.SHORT and signal.stop_loss <= signal.entry_price:
            return RiskDecision(approved=False, reason="stop-loss is not above entry for a SHORT")

        if state.realized_pnl_today_inr <= -self.settings.daily_loss_ceiling_inr:
            return RiskDecision(
                approved=False,
                reason=(
                    f"daily loss ceiling breached "
                    f"(realized {state.realized_pnl_today_inr:.2f} <= -{self.settings.daily_loss_ceiling_inr:.2f}); "
                    f"no new trades today"
                ),
            )

        if state.realized_pnl_this_week_inr <= -self.settings.weekly_loss_ceiling_inr:
            return RiskDecision(
                approved=False,
                reason=(
                    f"weekly drawdown ceiling breached "
                    f"(realized {state.realized_pnl_this_week_inr:.2f} <= -{self.settings.weekly_loss_ceiling_inr:.2f}); "
                    f"no new trades this week"
                ),
            )

        if len(state.open_positions) >= self.settings.max_open_positions:
            return RiskDecision(
                approved=False,
                reason=f"max open positions reached ({self.settings.max_open_positions})",
            )

        if state.trades_taken_today >= self.settings.max_trades_per_day:
            return RiskDecision(
                approved=False,
                reason=f"max trades per day reached ({self.settings.max_trades_per_day})",
            )

        for pos in state.open_positions:
            if pos.symbol == signal.symbol:
                return RiskDecision(
                    approved=False,
                    reason=(
                        f"a position in {signal.symbol} is already open — "
                        f"no averaging down / no adding to an existing position"
                    ),
                )

        risk_per_share = abs(signal.entry_price - signal.stop_loss)
        if risk_per_share <= 0:
            return RiskDecision(approved=False, reason="zero risk-per-share (stop equals entry)")

        risk_budget_inr = state.active_trading_capital_inr * (self.settings.risk_per_trade_pct / 100.0)
        quantity_by_risk = math.floor(risk_budget_inr / risk_per_share)

        # No leverage: cash outlay for the position must not exceed available
        # active trading capital.
        quantity_by_cash = math.floor(state.active_trading_capital_inr / signal.entry_price)

        quantity = max(0, min(quantity_by_risk, quantity_by_cash))

        if quantity < 1:
            return RiskDecision(
                approved=False,
                reason=(
                    f"position size rounds to 0 shares at {self.settings.risk_per_trade_pct}% risk "
                    f"(active capital ₹{state.active_trading_capital_inr:.2f}, "
                    f"risk/share ₹{risk_per_share:.2f}, entry ₹{signal.entry_price:.2f}) — "
                    f"account too small for this trade's stop distance"
                ),
            )

        actual_risk_inr = round(quantity * risk_per_share, 2)
        cash_outlay_inr = round(quantity * signal.entry_price, 2)
        if cash_outlay_inr > state.active_trading_capital_inr + 1e-6:
            return RiskDecision(approved=False, reason="cash outlay would exceed active trading capital (no leverage)")

        return RiskDecision(approved=True, quantity=quantity, risk_amount_inr=actual_risk_inr, reason="approved")
