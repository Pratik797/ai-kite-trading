from __future__ import annotations

import pandas as pd

from app.backtest.benchmark import buy_and_hold_return_pct


def _df(closes: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="D")
    return pd.DataFrame({"close": closes}, index=idx)


def test_full_series_return_matches_first_to_last_close():
    df = _df([100.0, 110.0, 90.0, 120.0])
    assert buy_and_hold_return_pct(df) == 20.0  # (120-100)/100 * 100


def test_windowed_return_uses_given_indices():
    df = _df([100.0, 110.0, 90.0, 120.0])
    # (90-110)/110 * 100
    assert buy_and_hold_return_pct(df, start_idx=1, end_idx=2) == -18.182


def test_empty_dataframe_returns_zero():
    df = _df([])
    assert buy_and_hold_return_pct(df) == 0.0


def test_zero_start_price_returns_zero_not_a_crash():
    df = _df([0.0, 50.0])
    assert buy_and_hold_return_pct(df) == 0.0
