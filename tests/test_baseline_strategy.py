from __future__ import annotations

import numpy as np
import pandas as pd

from app.db.models import SignalDirection
from app.strategy.baseline import generate_signal, generate_signals_for_series
from app.strategy.signals import TradeSignal


def _df(rows: list[dict]) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=len(rows), freq="D")
    return pd.DataFrame(rows, index=idx)


def test_long_signal_on_bullish_ema_crossover_with_confirmation():
    rows = [
        {"close": 100.0, "ema_fast": 99.0, "ema_slow": 100.0, "rsi_14": 55.0, "atr_14": 2.0, "vwap": 99.5},
        {"close": 102.0, "ema_fast": 101.0, "ema_slow": 100.5, "rsi_14": 55.0, "atr_14": 2.0, "vwap": 99.5},
    ]
    df = _df(rows)
    signal = generate_signal(df, "TEST", 1)
    assert signal.direction == SignalDirection.LONG
    assert signal.entry_price == 102.0
    assert signal.stop_loss == 102.0 - 1.5 * 2.0
    assert signal.target_price == 102.0 + 2.5 * 2.0
    assert signal.risk_reward_ratio > 1.0


def test_short_signal_on_bearish_ema_crossover_with_confirmation():
    rows = [
        {"close": 100.0, "ema_fast": 101.0, "ema_slow": 100.0, "rsi_14": 45.0, "atr_14": 1.5, "vwap": 100.5},
        {"close": 98.0, "ema_fast": 97.0, "ema_slow": 97.5, "rsi_14": 45.0, "atr_14": 1.5, "vwap": 100.5},
    ]
    df = _df(rows)
    signal = generate_signal(df, "TEST", 1)
    assert signal.direction == SignalDirection.SHORT
    assert signal.entry_price == 98.0
    assert signal.stop_loss == 98.0 + 1.5 * 1.5
    assert signal.target_price == 98.0 - 2.5 * 1.5


def test_no_trade_when_no_ema_crossover():
    rows = [
        {"close": 100.0, "ema_fast": 101.0, "ema_slow": 100.0, "rsi_14": 50.0, "atr_14": 1.0, "vwap": 100.0},
        {"close": 100.5, "ema_fast": 102.0, "ema_slow": 100.5, "rsi_14": 50.0, "atr_14": 1.0, "vwap": 100.0},
    ]
    df = _df(rows)
    signal = generate_signal(df, "TEST", 1)
    assert signal.direction == SignalDirection.NO_TRADE
    assert not signal.is_actionable


def test_no_trade_with_insufficient_history_at_bar_zero():
    rows = [{"close": 100.0, "ema_fast": 100.0, "ema_slow": 100.0, "rsi_14": 50.0, "atr_14": 1.0, "vwap": 100.0}]
    df = _df(rows)
    signal = generate_signal(df, "TEST", 0)
    assert signal.direction == SignalDirection.NO_TRADE


def test_no_trade_when_atr_is_zero():
    rows = [
        {"close": 100.0, "ema_fast": 99.0, "ema_slow": 100.0, "rsi_14": 55.0, "atr_14": 0.0, "vwap": 99.5},
        {"close": 102.0, "ema_fast": 101.0, "ema_slow": 100.5, "rsi_14": 55.0, "atr_14": 0.0, "vwap": 99.5},
    ]
    df = _df(rows)
    signal = generate_signal(df, "TEST", 1)
    assert signal.direction == SignalDirection.NO_TRADE


def test_no_lookahead_future_bars_never_change_a_past_signal():
    rows = [
        {"close": 100.0, "ema_fast": 99.0, "ema_slow": 100.0, "rsi_14": 55.0, "atr_14": 2.0, "vwap": 99.5},
        {"close": 102.0, "ema_fast": 101.0, "ema_slow": 100.5, "rsi_14": 55.0, "atr_14": 2.0, "vwap": 99.5},
    ]
    df_short = _df(rows)
    signal_short = generate_signal(df_short, "TEST", 1)

    # Append wildly different future data — it must not change bar 1's signal.
    extra_rows = rows + [
        {"close": 5000.0, "ema_fast": 1.0, "ema_slow": 9000.0, "rsi_14": 1.0, "atr_14": 500.0, "vwap": 1.0}
    ]
    df_long = _df(extra_rows)
    signal_long = generate_signal(df_long, "TEST", 1)

    assert signal_short.direction == signal_long.direction
    assert signal_short.entry_price == signal_long.entry_price
    assert signal_short.stop_loss == signal_long.stop_loss
    assert signal_short.target_price == signal_long.target_price


def test_generate_signals_for_series_smoke_and_includes_no_trade():
    rng = np.random.default_rng(42)
    n = 150
    idx = pd.date_range("2023-01-01", periods=n, freq="B")
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    high = close + rng.uniform(0, 1, n)
    low = close - rng.uniform(0, 1, n)
    open_ = close + rng.normal(0, 0.5, n)
    volume = rng.integers(1000, 5000, n)
    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=idx
    )
    signals = generate_signals_for_series(df, "TEST")
    assert len(signals) == n
    assert all(isinstance(s, TradeSignal) for s in signals)
    # A random walk should not produce a qualifying trend signal on every bar.
    assert any(s.direction == SignalDirection.NO_TRADE for s in signals)
