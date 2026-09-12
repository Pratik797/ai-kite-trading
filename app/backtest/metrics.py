"""
Backtest performance metrics (spec section 8: "Report return, drawdown, win
rate, expectancy, profit factor, turnover and losing streaks.")

All metrics are computed from NET P&L (after brokerage/STT/slippage) so they
reflect realistic, cost-inclusive performance rather than a gross-of-costs
number that would overstate any edge.
"""
from __future__ import annotations

from app.db.models import Trade


def compute_metrics(trades: list[Trade], initial_capital_inr: float) -> dict:
    closed = [t for t in trades if t.net_pnl_inr is not None]
    closed.sort(key=lambda t: t.exit_time)

    if not closed:
        return {
            "num_trades": 0,
            "total_return_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "win_rate_pct": 0.0,
            "expectancy_inr": 0.0,
            "expectancy_r": 0.0,
            "profit_factor": None,
            "turnover_ratio": 0.0,
            "longest_losing_streak": 0,
            "final_equity_inr": round(initial_capital_inr, 2),
        }

    equity = initial_capital_inr
    peak = equity
    max_drawdown_pct = 0.0
    wins: list[float] = []
    losses: list[float] = []
    streak = 0
    longest_losing_streak = 0
    total_notional = 0.0

    for t in closed:
        equity += t.net_pnl_inr
        peak = max(peak, equity)
        drawdown_pct = ((peak - equity) / peak * 100.0) if peak > 0 else 0.0
        max_drawdown_pct = max(max_drawdown_pct, drawdown_pct)

        if t.net_pnl_inr > 0:
            wins.append(t.net_pnl_inr)
            streak = 0
        else:
            losses.append(t.net_pnl_inr)
            streak += 1
            longest_losing_streak = max(longest_losing_streak, streak)

        total_notional += t.entry_price * t.quantity + (t.exit_price or 0.0) * t.quantity

    total_return_pct = (equity - initial_capital_inr) / initial_capital_inr * 100.0 if initial_capital_inr else 0.0
    win_rate_pct = len(wins) / len(closed) * 100.0
    expectancy_inr = sum(t.net_pnl_inr for t in closed) / len(closed)
    r_multiples = [t.net_pnl_inr / t.risk_amount_inr for t in closed if t.risk_amount_inr]
    expectancy_r = (sum(r_multiples) / len(r_multiples)) if r_multiples else 0.0
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    if gross_loss > 0:
        profit_factor: float | None = round(gross_profit / gross_loss, 3)
    elif gross_profit > 0:
        profit_factor = float("inf")
    else:
        profit_factor = None
    turnover_ratio = total_notional / initial_capital_inr if initial_capital_inr else 0.0

    return {
        "num_trades": len(closed),
        "total_return_pct": round(total_return_pct, 3),
        "max_drawdown_pct": round(max_drawdown_pct, 3),
        "win_rate_pct": round(win_rate_pct, 2),
        "expectancy_inr": round(expectancy_inr, 2),
        "expectancy_r": round(expectancy_r, 3),
        "profit_factor": profit_factor,
        "turnover_ratio": round(turnover_ratio, 3),
        "longest_losing_streak": longest_losing_streak,
        "final_equity_inr": round(equity, 2),
    }
