from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from app.db.models import SignalDirection


@dataclass
class TradeSignal:
    """In-memory representation of a candidate signal, before it's persisted
    or passed to the risk engine. Mirrors spec's Trade Signals module:
    entry/stop/target, risk/reward, confidence, expiry, approval state."""

    symbol: str
    timestamp: dt.datetime
    direction: SignalDirection
    entry_price: float
    stop_loss: float
    target_price: float
    confidence: float                     # 0.0-1.0; deterministic strategy uses a rule-based score
    reasons: list[str] = field(default_factory=list)
    contradictory_evidence: list[str] = field(default_factory=list)
    strategy_name: str = "baseline_ema_rsi_atr"
    expiry: dt.datetime | None = None      # signal is stale after this (e.g. next bar's open)

    @property
    def risk_reward_ratio(self) -> float:
        risk = abs(self.entry_price - self.stop_loss)
        reward = abs(self.target_price - self.entry_price)
        if risk == 0:
            return 0.0
        return round(reward / risk, 3)

    @property
    def is_actionable(self) -> bool:
        return self.direction != SignalDirection.NO_TRADE
