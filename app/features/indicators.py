"""
Technical feature engine (spec section 2: Market Scanner / section 5: "Python
calculates indicators"). Pure functions over a pandas DataFrame with columns
['open','high','low','close','volume'], indexed by timestamp.

Every function here is deterministic and has no dependency on Claude/AI —
this is the layer the AI Analysis module (Phase 5, not built in this pass)
would eventually receive compact structured output from, and it's also
exactly what the deterministic baseline strategy uses directly.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    result = 100 - (100 / (1 + rs))
    # avg_loss == 0 makes rs undefined via division, but it is NOT itself an
    # undefined/warmup case: zero losses in the lookback is a real (if
    # extreme) market state and RSI should read 100 there, not a neutral 50.
    # Only genuine warmup (both averages still NaN/zero at the series start)
    # falls back to neutral.
    no_losses = avg_loss == 0
    result = result.where(~no_losses, np.where(avg_gain > 0, 100.0, 50.0))
    return result.fillna(50.0)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def vwap(df: pd.DataFrame) -> pd.Series:
    """Session VWAP, reset at the start of each calendar day. Requires a
    DatetimeIndex."""
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    pv = typical_price * df["volume"]
    day = df.index.normalize() if hasattr(df.index, "normalize") else None
    if day is not None:
        cum_pv = pv.groupby(day).cumsum()
        cum_vol = df["volume"].groupby(day).cumsum()
    else:
        cum_pv = pv.cumsum()
        cum_vol = df["volume"].cumsum()
    return (cum_pv / cum_vol.replace(0, np.nan)).bfill()


def support_resistance(df: pd.DataFrame, window: int = 20) -> tuple[pd.Series, pd.Series]:
    """Rolling structural support/resistance: lowest low / highest high over
    the trailing window, excluding the current bar (so it's usable live
    without lookahead)."""
    support = df["low"].shift(1).rolling(window).min()
    resistance = df["high"].shift(1).rolling(window).max()
    return support, resistance


def add_all_features(df: pd.DataFrame) -> pd.DataFrame:
    """Attach the standard feature set used by the baseline strategy and, in
    later phases, by the compact context sent to Claude."""
    out = df.copy()
    out["ema_fast"] = ema(out["close"], 9)
    out["ema_slow"] = ema(out["close"], 21)
    out["rsi_14"] = rsi(out["close"], 14)
    out["atr_14"] = atr(out, 14)
    out["vwap"] = vwap(out)
    out["support"], out["resistance"] = support_resistance(out, 20)
    out["volume_avg_20"] = out["volume"].rolling(20).mean()
    return out


def classify_regime(df: pd.DataFrame, lookback: int = 50) -> str:
    """A simple, explainable market regime classifier (spec: 'market regime'
    on the Dashboard). Not a black box — just slope of a slow EMA relative to
    recent ATR, so it's auditable and testable."""
    if len(df) < lookback + 21:
        return "insufficient_data"
    recent = df.iloc[-lookback:]
    ema_slow = ema(recent["close"], 21)
    slope = (ema_slow.iloc[-1] - ema_slow.iloc[0]) / lookback
    avg_atr = atr(recent, 14).iloc[-1]
    if avg_atr == 0 or np.isnan(avg_atr):
        return "insufficient_data"
    normalized_slope = slope / avg_atr
    if normalized_slope > 0.15:
        return "trending_up"
    if normalized_slope < -0.15:
        return "trending_down"
    return "range_bound"
