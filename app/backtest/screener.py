"""
Symbol screener — added to answer a concrete question: is a symbol even
TRADEABLE at the current active capital and risk-per-trade percentage, or
will the baseline strategy's ATR-based stop distance always round position
size down to zero shares (as happened with RELIANCE.NS)?

This must be checked BEFORE backtesting a basket of symbols, otherwise an
untradeable symbol just contributes silent zero-trade noise to the
aggregate (or, worse, makes the basket look worse than the strategy really
is, when the true cause is account size, not the strategy).

"Typical" stop distance is the MEDIAN ATR(14) over the whole loaded
history multiplied by the baseline strategy's stop multiplier — the
median, not the latest bar, so a single unusually calm or volatile day
doesn't swing the screening result.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from app.config import Settings, settings as default_settings
from app.features.indicators import add_all_features
from app.strategy.baseline import ATR_STOP_MULTIPLIER


@dataclass
class ScreenResult:
    symbol: str
    last_close_inr: float
    median_atr_14_inr: float
    typical_stop_distance_inr: float
    quantity_by_risk_budget: int
    quantity_by_cash_no_leverage: int
    tradeable_quantity: int
    is_tradeable: bool


def screen_symbol(symbol: str, df: pd.DataFrame, settings: Settings | None = None) -> ScreenResult:
    """`df` is raw OHLCV (as returned by app.backtest.data.load_historical)."""
    s = settings or default_settings
    featured = add_all_features(df)
    atr_series = featured["atr_14"].dropna()
    last_close = float(featured["close"].iloc[-1])

    if atr_series.empty:
        return ScreenResult(symbol, round(last_close, 2), 0.0, 0.0, 0, 0, 0, False)

    median_atr = float(atr_series.median())
    stop_distance = median_atr * ATR_STOP_MULTIPLIER

    risk_budget_inr = s.active_trading_capital_inr * (s.risk_per_trade_pct / 100.0)
    qty_by_risk = math.floor(risk_budget_inr / stop_distance) if stop_distance > 0 else 0
    qty_by_cash = math.floor(s.active_trading_capital_inr / last_close) if last_close > 0 else 0
    tradeable_qty = max(0, min(qty_by_risk, qty_by_cash))

    return ScreenResult(
        symbol=symbol,
        last_close_inr=round(last_close, 2),
        median_atr_14_inr=round(median_atr, 2),
        typical_stop_distance_inr=round(stop_distance, 2),
        quantity_by_risk_budget=qty_by_risk,
        quantity_by_cash_no_leverage=qty_by_cash,
        tradeable_quantity=tradeable_qty,
        is_tradeable=tradeable_qty >= 1,
    )
