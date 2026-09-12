"""
Deterministic, technical-only baseline strategy — no AI/Claude involvement.

This exists for two reasons the spec is explicit about:
1. The decision pipeline must support NO TRADE, and every candidate signal
   needs a rule-based origin before any AI reasoning is layered on top
   (spec section 4/5).
2. Backtesting must compare a technical-only baseline against an
   AI/news-enhanced strategy (spec section 8) — you can't run that
   comparison if the "baseline" doesn't exist as real, testable code.

Rules (intentionally simple and auditable, not curve-fit):
  LONG  when: ema_fast crosses above ema_slow, RSI in [40, 65] (momentum
        without being overbought), close above VWAP, and regime is not
        trending_down.
  SHORT when: the mirror image of the above.
  Otherwise: NO_TRADE.

Stop-loss = entry -/+ 1.5x ATR(14). Target = entry +/- 2.5x ATR(14), giving a
baseline risk/reward near 1.67 before costs. These multipliers are
parameters, not guarantees — spec section 8 requires validating them via
backtesting, not trusting them by construction.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from app.db.models import SignalDirection
from app.features.indicators import add_all_features, classify_regime
from app.strategy.signals import TradeSignal

ATR_STOP_MULTIPLIER = 1.5
ATR_TARGET_MULTIPLIER = 2.5
RSI_LONG_RANGE = (40.0, 65.0)
RSI_SHORT_RANGE = (35.0, 60.0)


def generate_signal(df: pd.DataFrame, symbol: str, as_of_index: int) -> TradeSignal:
    """Generate a signal using only data up to and including `as_of_index`
    (no lookahead — critical for a backtest to be honest). `df` must already
    have features attached via add_all_features(); `as_of_index` is a
    positional row index into `df`.
    """
    if as_of_index < 1 or as_of_index >= len(df):
        return _no_trade(symbol, df.index[max(as_of_index, 0)], ["insufficient history"])

    row = df.iloc[as_of_index]
    prev = df.iloc[as_of_index - 1]
    ts = df.index[as_of_index]

    required = ["ema_fast", "ema_slow", "rsi_14", "atr_14", "vwap", "close"]
    if row[required].isna().any() or prev[["ema_fast", "ema_slow"]].isna().any():
        return _no_trade(symbol, ts, ["indicators not yet warmed up"])

    regime = classify_regime(df.iloc[: as_of_index + 1])

    crossed_up = prev["ema_fast"] <= prev["ema_slow"] and row["ema_fast"] > row["ema_slow"]
    crossed_down = prev["ema_fast"] >= prev["ema_slow"] and row["ema_fast"] < row["ema_slow"]

    atr_val = float(row["atr_14"])
    if atr_val <= 0:
        return _no_trade(symbol, ts, ["ATR is zero — no meaningful volatility to size a stop"])

    if crossed_up and RSI_LONG_RANGE[0] <= row["rsi_14"] <= RSI_LONG_RANGE[1] and row["close"] > row["vwap"] and regime != "trending_down":
        entry = float(row["close"])
        stop = entry - ATR_STOP_MULTIPLIER * atr_val
        target = entry + ATR_TARGET_MULTIPLIER * atr_val
        reasons = [
            "EMA(9) crossed above EMA(21)",
            f"RSI(14)={row['rsi_14']:.1f} within bullish-but-not-overbought range",
            "close above session VWAP",
            f"regime={regime}",
        ]
        contradictions = []
        if regime == "range_bound":
            contradictions.append("regime classified as range-bound, not confirmed trend")
        return TradeSignal(
            symbol=symbol, timestamp=ts, direction=SignalDirection.LONG,
            entry_price=entry, stop_loss=stop, target_price=target,
            confidence=_confidence(regime, row), reasons=reasons,
            contradictory_evidence=contradictions,
            expiry=ts + dt.timedelta(days=1),
        )

    if crossed_down and RSI_SHORT_RANGE[0] <= row["rsi_14"] <= RSI_SHORT_RANGE[1] and row["close"] < row["vwap"] and regime != "trending_up":
        entry = float(row["close"])
        stop = entry + ATR_STOP_MULTIPLIER * atr_val
        target = entry - ATR_TARGET_MULTIPLIER * atr_val
        reasons = [
            "EMA(9) crossed below EMA(21)",
            f"RSI(14)={row['rsi_14']:.1f} within bearish-but-not-oversold range",
            "close below session VWAP",
            f"regime={regime}",
        ]
        contradictions = []
        if regime == "range_bound":
            contradictions.append("regime classified as range-bound, not confirmed trend")
        return TradeSignal(
            symbol=symbol, timestamp=ts, direction=SignalDirection.SHORT,
            entry_price=entry, stop_loss=stop, target_price=target,
            confidence=_confidence(regime, row), reasons=reasons,
            contradictory_evidence=contradictions,
            expiry=ts + dt.timedelta(days=1),
        )

    return _no_trade(symbol, ts, ["no qualifying EMA crossover with confirming RSI/VWAP/regime"])


def generate_signals_for_series(df: pd.DataFrame, symbol: str) -> list[TradeSignal]:
    """Run the strategy across an entire historical series (used by the
    backtest engine). Returns one signal per bar, including NO_TRADE bars,
    so the full decision history is auditable."""
    featured = add_all_features(df)
    return [generate_signal(featured, symbol, i) for i in range(len(featured))]


def _confidence(regime: str, row: pd.Series) -> float:
    """A simple, explainable confidence score — not an ML prediction.
    Higher when the trend regime agrees with the signal direction."""
    base = 0.5
    if regime in ("trending_up", "trending_down"):
        base += 0.2
    rsi_extremity = abs(row["rsi_14"] - 50) / 50  # 0 at neutral, up to 1 at extremes
    base += 0.1 * (1 - rsi_extremity)  # slightly favor moderate RSI over extremes
    return round(min(max(base, 0.0), 1.0), 3)


def _no_trade(symbol: str, ts, reasons: list[str]) -> TradeSignal:
    return TradeSignal(
        symbol=symbol, timestamp=ts, direction=SignalDirection.NO_TRADE,
        entry_price=0.0, stop_loss=0.0, target_price=0.0,
        confidence=0.0, reasons=reasons,
    )
