"""
Buy-and-hold benchmark. An absolute strategy return means little on its
own — this gives the honest baseline it must beat: what a passive investor
would have made just holding the same symbol over the same window, with no
strategy, no signals, and no trading costs.
"""
from __future__ import annotations

import pandas as pd


def buy_and_hold_return_pct(df: pd.DataFrame, start_idx: int = 0, end_idx: int | None = None) -> float:
    """Percent price return from `start_idx` to `end_idx` (inclusive),
    defaulting to the full series. Uses the same integer bar-index
    convention as the backtest engine's in-sample/out-of-sample split, so
    callers can compute matching windows."""
    if len(df) == 0:
        return 0.0
    end_idx = len(df) - 1 if end_idx is None else end_idx
    start_price = float(df["close"].iloc[start_idx])
    end_price = float(df["close"].iloc[end_idx])
    if start_price == 0:
        return 0.0
    return round((end_price - start_price) / start_price * 100.0, 3)
