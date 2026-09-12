from __future__ import annotations

import datetime as dt

from app.backtest.metrics import compute_metrics
from app.db.models import SignalDirection, Trade, TradeStatus


def _trade(net_pnl: float, exit_time: dt.datetime, risk_amount_inr: float = 50.0) -> Trade:
    return Trade(
        symbol="TEST",
        direction=SignalDirection.LONG,
        entry_time=exit_time - dt.timedelta(days=1),
        entry_price=100.0,
        quantity=10,
        stop_loss=90.0,
        target_price=110.0,
        risk_amount_inr=risk_amount_inr,
        exit_time=exit_time,
        exit_price=100.0 + net_pnl / 10,
        status=TradeStatus.CLOSED_TARGET,
        gross_pnl_inr=net_pnl,
        costs_inr=0.0,
        net_pnl_inr=net_pnl,
        is_paper=True,
    )


def test_compute_metrics_on_a_hand_computed_fixture():
    trades = [
        _trade(50.0, dt.datetime(2024, 1, 2)),
        _trade(-20.0, dt.datetime(2024, 1, 3)),
        _trade(-10.0, dt.datetime(2024, 1, 4)),
        _trade(30.0, dt.datetime(2024, 1, 5)),
    ]
    metrics = compute_metrics(trades, initial_capital_inr=1000.0)

    assert metrics["num_trades"] == 4
    assert metrics["win_rate_pct"] == 50.0
    assert metrics["expectancy_inr"] == 12.5  # (50-20-10+30)/4
    assert abs(metrics["expectancy_r"] - 0.25) < 1e-9  # avg of [1, -0.4, -0.2, 0.6]
    assert abs(metrics["profit_factor"] - (80 / 30)) < 0.01
    assert metrics["longest_losing_streak"] == 2
    assert metrics["total_return_pct"] == 5.0  # (1050-1000)/1000 * 100
    assert metrics["final_equity_inr"] == 1050.0
    # equity path 1000->1050(peak)->1030->1020->1050; worst drawdown at 1020 vs peak 1050
    assert abs(metrics["max_drawdown_pct"] - ((1050 - 1020) / 1050 * 100)) < 1e-3


def test_compute_metrics_handles_no_trades():
    metrics = compute_metrics([], initial_capital_inr=1000.0)
    assert metrics["num_trades"] == 0
    assert metrics["profit_factor"] is None
    assert metrics["total_return_pct"] == 0.0


def test_compute_metrics_all_wins_gives_infinite_profit_factor():
    trades = [_trade(10.0, dt.datetime(2024, 1, 2)), _trade(5.0, dt.datetime(2024, 1, 3))]
    metrics = compute_metrics(trades, initial_capital_inr=1000.0)
    assert metrics["profit_factor"] == float("inf")
    assert metrics["longest_losing_streak"] == 0


def test_longest_losing_streak_counts_consecutive_losses_only():
    trades = [
        _trade(-5.0, dt.datetime(2024, 1, 2)),
        _trade(-5.0, dt.datetime(2024, 1, 3)),
        _trade(-5.0, dt.datetime(2024, 1, 4)),
        _trade(20.0, dt.datetime(2024, 1, 5)),
        _trade(-5.0, dt.datetime(2024, 1, 6)),
    ]
    metrics = compute_metrics(trades, initial_capital_inr=1000.0)
    assert metrics["longest_losing_streak"] == 3
