"""
Alternative strategy variants — built to test distinct HYPOTHESES against
the baseline EMA-crossover strategy's "no consistent edge" basket result,
not to tune the baseline's own constants (that would just be curve-fitting
against data we already know shows no edge).

Both variants share the baseline's TradeSignal contract, the same
no-lookahead guarantee (each depends only on df.iloc[as_of_index] and
prior rows — the underlying indicator columns are themselves already
lookahead-safe, see app.features.indicators), and are pluggable into
app.backtest.engine.BacktestEngine via its `signal_fn` parameter.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from app.db.models import SignalDirection
from app.features.indicators import classify_regime
from app.strategy.baseline import (
    ATR_STOP_MULTIPLIER,
    ATR_TARGET_MULTIPLIER,
    RSI_LONG_RANGE,
    RSI_SHORT_RANGE,
)
from app.strategy.signals import TradeSignal

# --- Variant A: mean reversion off rolling support/resistance ----------
#
# Opposite hypothesis to the trend-following baseline: on choppy/
# range-bound names (exactly the regime the baseline's crossover rules
# found nothing in), price stretched to an oversold/overbought extreme AT
# the rolling support/resistance level might bounce back toward the range
# rather than break out. Stops are tighter and targets smaller than the
# baseline's, deliberately: a counter-trend bounce trade should risk less
# and expect a smaller move than a trend-following breakout trade.

RSI_OVERSOLD = 30.0
RSI_OVERBOUGHT = 70.0
MR_ATR_STOP_MULTIPLIER = 1.0
MR_ATR_TARGET_MULTIPLIER = 1.5
SUPPORT_RESISTANCE_PROXIMITY = 0.01  # within 1% of the rolling level counts as "at" it

MEAN_REVERSION_STRATEGY_NAME = "mean_reversion_support_resistance"


def generate_signal_mean_reversion(df: pd.DataFrame, symbol: str, as_of_index: int) -> TradeSignal:
    if as_of_index < 1 or as_of_index >= len(df):
        return _no_trade(symbol, df.index[max(as_of_index, 0)], ["insufficient history"], MEAN_REVERSION_STRATEGY_NAME)

    row = df.iloc[as_of_index]
    ts = df.index[as_of_index]

    required = ["close", "rsi_14", "atr_14", "support", "resistance"]
    if row[required].isna().any():
        return _no_trade(symbol, ts, ["indicators not yet warmed up"], MEAN_REVERSION_STRATEGY_NAME)

    atr_val = float(row["atr_14"])
    if atr_val <= 0:
        return _no_trade(symbol, ts, ["ATR is zero"], MEAN_REVERSION_STRATEGY_NAME)

    close = float(row["close"])
    support = float(row["support"])
    resistance = float(row["resistance"])
    rsi = float(row["rsi_14"])

    near_support = close <= support * (1 + SUPPORT_RESISTANCE_PROXIMITY)
    near_resistance = close >= resistance * (1 - SUPPORT_RESISTANCE_PROXIMITY)

    if near_support and rsi <= RSI_OVERSOLD:
        entry = close
        stop = entry - MR_ATR_STOP_MULTIPLIER * atr_val
        target = entry + MR_ATR_TARGET_MULTIPLIER * atr_val
        return TradeSignal(
            symbol=symbol,
            timestamp=ts,
            direction=SignalDirection.LONG,
            entry_price=entry,
            stop_loss=stop,
            target_price=target,
            confidence=0.5,
            reasons=[
                f"close {close:.2f} at/below rolling support {support:.2f}",
                f"RSI(14)={rsi:.1f} oversold",
            ],
            strategy_name=MEAN_REVERSION_STRATEGY_NAME,
            expiry=ts + dt.timedelta(days=1),
        )

    if near_resistance and rsi >= RSI_OVERBOUGHT:
        entry = close
        stop = entry + MR_ATR_STOP_MULTIPLIER * atr_val
        target = entry - MR_ATR_TARGET_MULTIPLIER * atr_val
        return TradeSignal(
            symbol=symbol,
            timestamp=ts,
            direction=SignalDirection.SHORT,
            entry_price=entry,
            stop_loss=stop,
            target_price=target,
            confidence=0.5,
            reasons=[
                f"close {close:.2f} at/above rolling resistance {resistance:.2f}",
                f"RSI(14)={rsi:.1f} overbought",
            ],
            strategy_name=MEAN_REVERSION_STRATEGY_NAME,
            expiry=ts + dt.timedelta(days=1),
        )

    return _no_trade(symbol, ts, ["no qualifying oversold/overbought bounce at support/resistance"], MEAN_REVERSION_STRATEGY_NAME)


# --- Variant B: regime-gated trend following ----------------------------
#
# Identical EMA-crossover/RSI/VWAP entry logic to the baseline. The ONLY
# change: the baseline's soft regime gate ("not the opposite regime" —
# which lets range_bound and insufficient_data through) is replaced with a
# strict gate that requires classify_regime() to STRONGLY agree
# (trending_up for a LONG, trending_down for a SHORT) and skips
# range_bound/insufficient_data entirely. Tests whether the baseline's
# rules were diluted by trading in the wrong regime, rather than being
# wrong in principle.

REGIME_GATED_TREND_STRATEGY_NAME = "regime_gated_trend"


def generate_signal_regime_gated_trend(df: pd.DataFrame, symbol: str, as_of_index: int) -> TradeSignal:
    if as_of_index < 1 or as_of_index >= len(df):
        return _no_trade(symbol, df.index[max(as_of_index, 0)], ["insufficient history"], REGIME_GATED_TREND_STRATEGY_NAME)

    row = df.iloc[as_of_index]
    prev = df.iloc[as_of_index - 1]
    ts = df.index[as_of_index]

    required = ["ema_fast", "ema_slow", "rsi_14", "atr_14", "vwap", "close"]
    if row[required].isna().any() or prev[["ema_fast", "ema_slow"]].isna().any():
        return _no_trade(symbol, ts, ["indicators not yet warmed up"], REGIME_GATED_TREND_STRATEGY_NAME)

    atr_val = float(row["atr_14"])
    if atr_val <= 0:
        return _no_trade(symbol, ts, ["ATR is zero"], REGIME_GATED_TREND_STRATEGY_NAME)

    regime = classify_regime(df.iloc[: as_of_index + 1])
    if regime not in ("trending_up", "trending_down"):
        return _no_trade(symbol, ts, [f"regime={regime}, skipped entirely (strong-agreement gate)"], REGIME_GATED_TREND_STRATEGY_NAME)

    crossed_up = prev["ema_fast"] <= prev["ema_slow"] and row["ema_fast"] > row["ema_slow"]
    crossed_down = prev["ema_fast"] >= prev["ema_slow"] and row["ema_fast"] < row["ema_slow"]

    if (
        regime == "trending_up"
        and crossed_up
        and RSI_LONG_RANGE[0] <= row["rsi_14"] <= RSI_LONG_RANGE[1]
        and row["close"] > row["vwap"]
    ):
        entry = float(row["close"])
        stop = entry - ATR_STOP_MULTIPLIER * atr_val
        target = entry + ATR_TARGET_MULTIPLIER * atr_val
        return TradeSignal(
            symbol=symbol,
            timestamp=ts,
            direction=SignalDirection.LONG,
            entry_price=entry,
            stop_loss=stop,
            target_price=target,
            confidence=0.6,
            reasons=[
                "EMA(9) crossed above EMA(21)",
                f"RSI(14)={row['rsi_14']:.1f} within bullish range",
                "close above VWAP",
                "regime strongly agrees: trending_up",
            ],
            strategy_name=REGIME_GATED_TREND_STRATEGY_NAME,
            expiry=ts + dt.timedelta(days=1),
        )

    if (
        regime == "trending_down"
        and crossed_down
        and RSI_SHORT_RANGE[0] <= row["rsi_14"] <= RSI_SHORT_RANGE[1]
        and row["close"] < row["vwap"]
    ):
        entry = float(row["close"])
        stop = entry + ATR_STOP_MULTIPLIER * atr_val
        target = entry - ATR_TARGET_MULTIPLIER * atr_val
        return TradeSignal(
            symbol=symbol,
            timestamp=ts,
            direction=SignalDirection.SHORT,
            entry_price=entry,
            stop_loss=stop,
            target_price=target,
            confidence=0.6,
            reasons=[
                "EMA(9) crossed below EMA(21)",
                f"RSI(14)={row['rsi_14']:.1f} within bearish range",
                "close below VWAP",
                "regime strongly agrees: trending_down",
            ],
            strategy_name=REGIME_GATED_TREND_STRATEGY_NAME,
            expiry=ts + dt.timedelta(days=1),
        )

    return _no_trade(symbol, ts, ["no qualifying regime-confirmed crossover"], REGIME_GATED_TREND_STRATEGY_NAME)


def _no_trade(symbol: str, ts, reasons: list[str], strategy_name: str) -> TradeSignal:
    return TradeSignal(
        symbol=symbol,
        timestamp=ts,
        direction=SignalDirection.NO_TRADE,
        entry_price=0.0,
        stop_loss=0.0,
        target_price=0.0,
        confidence=0.0,
        reasons=reasons,
        strategy_name=strategy_name,
    )
