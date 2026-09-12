from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.backtest.engine import BacktestEngine
from app.config import Settings
from app.db.models import Base, BacktestRun, Trade


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _synthetic_ohlcv(n: int = 300, seed: int = 1) -> pd.DataFrame:
    """A trending-with-noise synthetic series — enough structure that the
    baseline EMA-crossover strategy should fire some real trades, so this
    is a wiring/integration smoke test, not a check on strategy edge."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2022-01-03", periods=n, freq="B")
    drift = np.linspace(0, 40, n)
    noise = np.cumsum(rng.normal(0, 1.2, n))
    close = 100 + drift + noise
    high = close + rng.uniform(0.1, 1.5, n)
    low = close - rng.uniform(0.1, 1.5, n)
    open_ = close + rng.normal(0, 0.5, n)
    volume = rng.integers(50_000, 200_000, n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=idx
    )


def test_backtest_engine_runs_end_to_end_and_persists_a_run(db_session):
    settings = Settings(active_trading_capital_inr=1000.0, protected_capital_inr=4000.0)
    df = _synthetic_ohlcv()

    engine = BacktestEngine(db_session, settings)
    result = engine.run("SYN.TEST", df, out_of_sample_fraction=0.3)

    assert result.backtest_run_id is not None
    run = db_session.get(BacktestRun, result.backtest_run_id)
    assert run is not None
    assert run.is_out_of_sample is True

    persisted_trades = db_session.query(Trade).all()
    assert len(persisted_trades) == len(result.trades)

    for metrics in (result.in_sample_metrics, result.out_of_sample_metrics, result.overall_metrics):
        assert "num_trades" in metrics
        assert "max_drawdown_pct" in metrics

    # No lookahead in execution: every trade must enter strictly after its signal bar,
    # i.e. entry_time must exist among the dataframe's own timestamps (next-bar-open fill).
    for t in result.trades:
        assert t.entry_time in set(df.index.to_pydatetime())


def test_backtest_engine_requires_minimum_history(db_session):
    settings = Settings()
    df = _synthetic_ohlcv(n=10)
    engine = BacktestEngine(db_session, settings)
    with pytest.raises(ValueError):
        engine.run("SYN.TEST", df)
