from __future__ import annotations

import numpy as np
import pandas as pd

from app.features.indicators import (
    atr,
    classify_regime,
    ema,
    rsi,
    support_resistance,
    vwap,
)


def test_ema_first_value_equals_first_input():
    s = pd.Series([10.0, 20.0, 30.0])
    result = ema(s, period=3)
    assert result.iloc[0] == 10.0


def test_ema_tracks_a_rising_series_upward():
    s = pd.Series([float(i) for i in range(1, 20)])
    result = ema(s, period=5)
    assert result.iloc[-1] > result.iloc[0]


def test_rsi_all_gains_is_close_to_100():
    s = pd.Series([float(i) for i in range(1, 30)])  # strictly increasing
    result = rsi(s, period=14)
    assert result.iloc[-1] > 95.0


def test_rsi_all_losses_is_close_to_0():
    s = pd.Series([float(i) for i in range(30, 1, -1)])  # strictly decreasing
    result = rsi(s, period=14)
    assert result.iloc[-1] < 5.0


def test_rsi_flat_series_is_neutral_fifty():
    s = pd.Series([10.0] * 20)
    result = rsi(s, period=14)
    assert result.iloc[-1] == 50.0


def test_atr_constant_true_range():
    df = pd.DataFrame({"high": [10.0] * 5, "low": [8.0] * 5, "close": [9.0] * 5})
    result = atr(df, period=2)
    assert all(abs(v - 2.0) < 1e-9 for v in result)


def test_vwap_resets_each_calendar_day():
    idx = pd.to_datetime(
        ["2024-01-01 09:15", "2024-01-01 10:15", "2024-01-02 09:15", "2024-01-02 10:15"]
    )
    df = pd.DataFrame(
        {
            "high": [101.0, 103.0, 50.0, 52.0],
            "low": [99.0, 101.0, 48.0, 50.0],
            "close": [100.0, 102.0, 49.0, 51.0],
            "volume": [1000, 1000, 1000, 1000],
        },
        index=idx,
    )
    result = vwap(df)
    day1_typical = (101.0 + 99.0 + 100.0) / 3
    assert abs(result.iloc[0] - day1_typical) < 1e-9
    day2_typical = (50.0 + 48.0 + 49.0) / 3
    assert abs(result.iloc[2] - day2_typical) < 1e-9


def test_support_resistance_excludes_current_bar_no_lookahead():
    df = pd.DataFrame({"high": [10.0, 20.0, 30.0, 5.0], "low": [9.0, 19.0, 29.0, 4.0]})
    support, resistance = support_resistance(df, window=3)
    # Row 3's resistance must come from rows 0-2 only (30.0), never its own high (5.0).
    assert resistance.iloc[3] == 30.0
    assert support.iloc[3] == 9.0


def test_support_resistance_unaffected_by_future_rows_appended():
    base = pd.DataFrame({"high": [10.0, 20.0, 30.0, 25.0], "low": [9.0, 19.0, 29.0, 24.0]})
    extended = pd.concat(
        [base, pd.DataFrame({"high": [999.0], "low": [1.0]})], ignore_index=True
    )
    support_base, resistance_base = support_resistance(base, window=3)
    support_ext, resistance_ext = support_resistance(extended, window=3)
    assert resistance_base.iloc[3] == resistance_ext.iloc[3]
    assert support_base.iloc[3] == support_ext.iloc[3]


def test_classify_regime_insufficient_data_on_short_series():
    df = pd.DataFrame({"high": [10.0] * 10, "low": [9.0] * 10, "close": [9.5] * 10})
    assert classify_regime(df, lookback=50) == "insufficient_data"


def test_classify_regime_trending_up():
    n = 80
    close = pd.Series(np.linspace(100, 160, n))
    df = pd.DataFrame({"high": close + 1, "low": close - 1, "close": close})
    assert classify_regime(df, lookback=50) == "trending_up"


def test_classify_regime_trending_down():
    n = 80
    close = pd.Series(np.linspace(160, 100, n))
    df = pd.DataFrame({"high": close + 1, "low": close - 1, "close": close})
    assert classify_regime(df, lookback=50) == "trending_down"


def test_classify_regime_range_bound():
    n = 80
    rng = np.random.default_rng(7)
    close = pd.Series(100 + rng.normal(0, 0.05, n))  # tiny noise, no drift
    df = pd.DataFrame({"high": close + 0.2, "low": close - 0.2, "close": close})
    assert classify_regime(df, lookback=50) == "range_bound"
