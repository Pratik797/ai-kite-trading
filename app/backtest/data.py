"""
Historical data loader — FOR BACKTESTING ONLY.

Uses the free `yfinance` package as a substitute for Kite Connect's paid
historical-data API (spec section 10), so the deterministic strategy and
backtest engine can be built and tested without live broker credentials.
This is NOT a production/live data feed: it is delayed, sourced from Yahoo
Finance, and unsuited to paper/live trading — a real Kite Connect market
data adapter is a later phase (see ROADMAP.md).

NSE tickers are addressed Yahoo-style, e.g. "RELIANCE.NS", "TCS.NS",
"HDFCBANK.NS".
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import yfinance as yf

CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data_cache"

REQUIRED_COLUMNS = ["open", "high", "low", "close", "volume"]


def load_historical(
    symbol: str,
    start: str,
    end: str,
    *,
    interval: str = "1d",
    use_cache: bool = True,
) -> pd.DataFrame:
    """Return OHLCV data indexed by timestamp with lowercase columns
    ['open','high','low','close','volume'], as required by
    app.features.indicators. `start`/`end` are 'YYYY-MM-DD' strings.

    Caches to a local CSV under data_cache/ (gitignored) so repeated
    backtest runs during development don't re-hit the network."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"{symbol.replace('.', '_')}_{interval}_{start}_{end}.csv"

    if use_cache and cache_file.exists():
        df = pd.read_csv(cache_file, index_col=0, parse_dates=True)
    else:
        raw = yf.download(
            symbol, start=start, end=end, interval=interval, progress=False, auto_adjust=True,
        )
        if raw.empty:
            raise ValueError(f"yfinance returned no data for {symbol} between {start} and {end}")
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        df = raw.rename(columns=str.lower)
        missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(f"yfinance data for {symbol} is missing expected columns: {missing}")
        df = df[REQUIRED_COLUMNS]
        df.to_csv(cache_file)

    df.index.name = "timestamp"
    return df
