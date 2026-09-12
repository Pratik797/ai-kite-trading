from __future__ import annotations

import numpy as np
import pandas as pd

from app.backtest.screener import screen_symbol
from app.config import Settings


def _settings(**overrides) -> Settings:
    defaults = dict(active_trading_capital_inr=1000.0, risk_per_trade_pct=1.5)
    defaults.update(overrides)
    return Settings(**defaults)


def _ohlcv(close: np.ndarray, atr_width: float) -> pd.DataFrame:
    idx = pd.date_range("2022-01-03", periods=len(close), freq="B")
    high = close + atr_width
    low = close - atr_width
    return pd.DataFrame(
        {"open": close, "high": high, "low": low, "close": close, "volume": np.full(len(close), 100_000)},
        index=idx,
    )


def test_expensive_stock_with_wide_stops_is_not_tradeable_at_tiny_capital():
    # Mirrors the real RELIANCE.NS finding: high price, wide ATR-based stop
    # distance, tiny active capital -> position size rounds to 0 shares.
    close = np.full(120, 1200.0) + np.random.default_rng(1).normal(0, 5, 120)
    df = _ohlcv(close, atr_width=15.0)  # ATR ~ a few tens of rupees -> stop distance too wide for Rs 1000
    result = screen_symbol("EXPENSIVE.NS", df, _settings())
    assert not result.is_tradeable
    assert result.tradeable_quantity == 0


def test_cheap_stock_with_narrow_stops_is_tradeable_at_tiny_capital():
    close = np.full(120, 20.0) + np.random.default_rng(2).normal(0, 0.3, 120)
    df = _ohlcv(close, atr_width=0.3)
    result = screen_symbol("CHEAP.NS", df, _settings())
    assert result.is_tradeable
    assert result.tradeable_quantity >= 1


def test_quantity_is_capped_by_cash_when_stop_distance_is_tiny():
    # Risk budget alone would allow a huge quantity, but no-leverage cash
    # cap must bind: quantity * price <= active capital.
    close = np.full(120, 500.0)
    df = _ohlcv(close, atr_width=0.05)  # razor-thin ATR -> huge risk-based quantity
    settings = _settings(active_trading_capital_inr=1000.0, risk_per_trade_pct=50.0)
    result = screen_symbol("TIGHTSTOP.NS", df, settings)
    assert result.quantity_by_cash_no_leverage == 2  # floor(1000/500)
    assert result.tradeable_quantity <= result.quantity_by_cash_no_leverage


def test_zero_volatility_series_is_not_tradeable():
    # high == low == close every bar -> ATR is exactly 0 -> zero stop distance
    # -> position sizing by risk is undefined/zero, so this must not be
    # treated as tradeable (division-by-zero guarded, not silently allowed).
    df = _ohlcv(np.full(120, 100.0), atr_width=0.0)
    result = screen_symbol("FLAT.NS", df, _settings())
    assert result.median_atr_14_inr == 0.0
    assert not result.is_tradeable
