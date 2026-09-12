from __future__ import annotations

import numpy as np
import pandas as pd

from app.db.models import SignalDirection
from app.strategy.variants import (
    generate_signal_mean_reversion,
    generate_signal_regime_gated_trend,
)


def _mr_df(rows: list[dict]) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=len(rows), freq="D")
    return pd.DataFrame(rows, index=idx)


def test_mean_reversion_long_on_oversold_bounce_at_support():
    rows = [
        {"close": 96.0, "rsi_14": 45.0, "atr_14": 2.0, "support": 90.0, "resistance": 120.0},
        {"close": 95.5, "rsi_14": 25.0, "atr_14": 2.0, "support": 95.0, "resistance": 120.0},
    ]
    df = _mr_df(rows)
    signal = generate_signal_mean_reversion(df, "TEST", 1)
    assert signal.direction == SignalDirection.LONG
    assert signal.entry_price == 95.5
    assert signal.stop_loss == 95.5 - 1.0 * 2.0
    assert signal.target_price == 95.5 + 1.5 * 2.0


def test_mean_reversion_short_on_overbought_bounce_at_resistance():
    rows = [
        {"close": 104.0, "rsi_14": 55.0, "atr_14": 1.5, "support": 80.0, "resistance": 110.0},
        {"close": 109.5, "rsi_14": 75.0, "atr_14": 1.5, "support": 80.0, "resistance": 110.0},
    ]
    df = _mr_df(rows)
    signal = generate_signal_mean_reversion(df, "TEST", 1)
    assert signal.direction == SignalDirection.SHORT
    assert signal.stop_loss == 109.5 + 1.0 * 1.5
    assert signal.target_price == 109.5 - 1.5 * 1.5


def test_mean_reversion_no_trade_when_oversold_but_not_near_support():
    rows = [
        {"close": 96.0, "rsi_14": 45.0, "atr_14": 2.0, "support": 50.0, "resistance": 120.0},
        {"close": 95.5, "rsi_14": 25.0, "atr_14": 2.0, "support": 50.0, "resistance": 120.0},
    ]
    df = _mr_df(rows)
    signal = generate_signal_mean_reversion(df, "TEST", 1)
    assert signal.direction == SignalDirection.NO_TRADE


def test_mean_reversion_no_trade_when_near_support_but_not_oversold():
    rows = [
        {"close": 96.0, "rsi_14": 45.0, "atr_14": 2.0, "support": 95.0, "resistance": 120.0},
        {"close": 95.5, "rsi_14": 45.0, "atr_14": 2.0, "support": 95.0, "resistance": 120.0},
    ]
    df = _mr_df(rows)
    signal = generate_signal_mean_reversion(df, "TEST", 1)
    assert signal.direction == SignalDirection.NO_TRADE


def test_mean_reversion_no_trade_when_atr_zero():
    rows = [
        {"close": 96.0, "rsi_14": 45.0, "atr_14": 0.0, "support": 95.0, "resistance": 120.0},
        {"close": 95.5, "rsi_14": 25.0, "atr_14": 0.0, "support": 95.0, "resistance": 120.0},
    ]
    df = _mr_df(rows)
    signal = generate_signal_mean_reversion(df, "TEST", 1)
    assert signal.direction == SignalDirection.NO_TRADE


def _trend_df(close: np.ndarray, high_width: float, low_width: float) -> pd.DataFrame:
    idx = pd.date_range("2020-01-01", periods=len(close), freq="B")
    df = pd.DataFrame({"close": close, "high": close + high_width, "low": close - low_width}, index=idx)
    # Precomputed feature columns the entry-condition check reads directly
    # (independent of classify_regime, which recomputes its own from the
    # raw close/high/low columns above).
    df["ema_fast"] = close
    df["ema_slow"] = close
    df["rsi_14"] = 55.0
    df["atr_14"] = 2.0
    df["vwap"] = close - 1.0
    return df


def _set(df: pd.DataFrame, row_idx: int, **values) -> None:
    for col, val in values.items():
        df.iloc[row_idx, df.columns.get_loc(col)] = val


def test_regime_gated_trend_long_when_regime_strongly_agrees():
    n = 90
    close = np.linspace(100, 160, n)  # matches the indicators test that verifies this is "trending_up"
    df = _trend_df(close, 1.0, 1.0)
    last = n - 1
    _set(df, last - 1, ema_fast=99.0, ema_slow=100.0)
    _set(df, last, ema_fast=101.0, ema_slow=100.5, rsi_14=55.0)

    signal = generate_signal_regime_gated_trend(df, "TEST", last)
    assert signal.direction == SignalDirection.LONG


def test_regime_gated_trend_skips_range_bound_even_with_a_crossover():
    n = 90
    rng = np.random.default_rng(7)
    close = 100 + rng.normal(0, 0.05, n)  # matches the indicators test that verifies this is "range_bound"
    df = _trend_df(close, 0.2, 0.2)
    last = n - 1
    # Engineer what would otherwise be a qualifying bullish crossover.
    _set(df, last - 1, ema_fast=99.0, ema_slow=100.0)
    _set(df, last, ema_fast=101.0, ema_slow=100.5, rsi_14=55.0)

    signal = generate_signal_regime_gated_trend(df, "TEST", last)
    assert signal.direction == SignalDirection.NO_TRADE
    assert "skipped entirely" in signal.reasons[0]


def test_regime_gated_trend_no_trade_on_insufficient_history():
    n = 10
    close = np.linspace(100, 105, n)
    df = _trend_df(close, 1.0, 1.0)
    signal = generate_signal_regime_gated_trend(df, "TEST", n - 1)
    assert signal.direction == SignalDirection.NO_TRADE
    assert "skipped entirely" in signal.reasons[0]  # insufficient_data also fails the strict gate
