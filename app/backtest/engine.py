"""
Backtesting engine (spec section 2 "Backtesting" / section 8).

Replays the deterministic baseline strategy bar-by-bar over historical data:
  - No lookahead: a signal generated "as of" bar N only sees data through
    bar N (enforced in app.strategy.baseline, not re-implemented here).
  - Fills are simulated at the NEXT bar's open (never the same bar the
    signal fired on), with slippage and Zerodha-like costs via
    app.backtest.costs.
  - Every actionable signal is passed through app.risk.engine.RiskEngine for
    independent position sizing/approval before any trade opens — the risk
    engine can reject regardless of the signal's confidence.
  - Every closed trade's realized net P&L is fed into
    app.risk.profit_locker.ProfitLocker during the replay (not just tested
    in isolation), with lock cycles run on the configured cadence.
  - The data range is split into an in-sample window and a held-out
    out-of-sample window by date, and metrics are reported for both
    separately (spec section 8: walk-forward/out-of-sample validation).
    The baseline strategy's constants (ATR multipliers, RSI bands) are
    fixed heuristics, not fit to any data in this pass, so no tuning
    happens here — the split exists to catch regime-dependent
    overfitting-by-appearance and to leave the walk-forward structure in
    place for when parameters do get tuned later.

Simplification (documented, not hidden): the strategy is intraday-style
(signals expire ~1 day after they fire) but this pass backtests on DAILY
bars, since that's what's freely available with deep history from
yfinance. A position is therefore allowed to ride out its entry bar before
the "expiry" rule can force-close it at the next bar's close — this avoids
an artifact where a position opened on bar N immediately appears "expired"
because bar N+1's timestamp already exceeds signal_time + 1 day. True
intraday backtesting would need intraday-interval historical data (a
later-phase enhancement, e.g. once Kite's historical API is available).

Persists a BacktestRun (versioned parameter snapshot + computed metrics)
and one Trade row per closed position.
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field

import pandas as pd
from sqlalchemy.orm import Session

from app.backtest.costs import fill_cost
from app.backtest.metrics import compute_metrics
from app.config import Settings, settings as default_settings
from app.db.models import BacktestRun, SignalDirection, Trade, TradeStatus
from app.features.indicators import add_all_features
from app.risk.engine import OpenPosition, RiskEngine, RiskState
from app.risk.profit_locker import ProfitLocker
from app.strategy.baseline import generate_signal


@dataclass
class BacktestResult:
    backtest_run_id: int
    in_sample_metrics: dict
    out_of_sample_metrics: dict
    overall_metrics: dict
    split_date: str
    trades: list[Trade] = field(default_factory=list)
    in_sample_trades: list[Trade] = field(default_factory=list)
    out_of_sample_trades: list[Trade] = field(default_factory=list)


def _as_py_datetime(ts) -> dt.datetime:
    return ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts


class BacktestEngine:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or default_settings
        self.risk_engine = RiskEngine(self.settings)

    def run(
        self,
        symbol: str,
        df: pd.DataFrame,
        *,
        out_of_sample_fraction: float = 0.3,
        strategy_name: str = "baseline_ema_rsi_atr",
    ) -> BacktestResult:
        featured = add_all_features(df)
        if len(featured) < 60:
            raise ValueError("need at least ~60 bars for indicators to warm up and produce a meaningful backtest")

        split_idx = int(len(featured) * (1 - out_of_sample_fraction))
        split_ts = featured.index[split_idx]

        profit_locker = ProfitLocker(self.session, self.settings)

        open_positions: list[dict] = []
        trades: list[Trade] = []

        trades_today_count = 0
        realized_today = 0.0
        realized_this_week = 0.0
        last_day = None
        last_week = None
        last_lock_marker = None

        for i in range(len(featured)):
            ts = featured.index[i]
            bar_day = ts.date() if hasattr(ts, "date") else ts
            bar_week = ts.isocalendar()[:2] if hasattr(ts, "isocalendar") else None

            if last_day is not None and bar_day != last_day:
                trades_today_count = 0
                realized_today = 0.0
            if last_week is not None and bar_week != last_week:
                realized_this_week = 0.0
            last_day, last_week = bar_day, bar_week

            # --- 1. Manage open positions: check stop/target/expiry against this bar ---
            still_open = []
            for pos in open_positions:
                is_long = pos["direction"] == SignalDirection.LONG
                hit_stop = (featured["low"].iloc[i] <= pos["stop_loss"]) if is_long else (featured["high"].iloc[i] >= pos["stop_loss"])
                hit_target = (featured["high"].iloc[i] >= pos["target_price"]) if is_long else (featured["low"].iloc[i] <= pos["target_price"])
                expired = pos["expiry"] is not None and ts >= pos["expiry"] and i > pos["entry_bar_index"]

                exit_price, status = None, None
                if hit_stop:
                    # If both stop and target fall inside this bar's range, we can't know
                    # which was touched first from OHLC data alone — stop-loss is checked
                    # first so we assume the conservative (worse-for-the-trader) outcome.
                    exit_price, status = pos["stop_loss"], TradeStatus.CLOSED_STOP
                elif hit_target:
                    exit_price, status = pos["target_price"], TradeStatus.CLOSED_TARGET
                elif expired:
                    exit_price, status = float(featured["close"].iloc[i]), TradeStatus.CLOSED_EOD

                if exit_price is not None:
                    is_buy_exit = not is_long  # closing a SHORT = buying back; closing a LONG = selling
                    fc = fill_cost(exit_price, pos["quantity"], is_buy=is_buy_exit, settings=self.settings)
                    if is_long:
                        gross = (fc.filled_price - pos["entry_fill_price"]) * pos["quantity"]
                    else:
                        gross = (pos["entry_fill_price"] - fc.filled_price) * pos["quantity"]
                    total_costs = round(pos["entry_costs_inr"] + fc.total_cost_inr, 2)
                    net = round(gross - total_costs, 2)

                    trade = Trade(
                        symbol=symbol,
                        direction=pos["direction"],
                        entry_time=pos["entry_time"],
                        entry_price=pos["entry_fill_price"],
                        quantity=pos["quantity"],
                        stop_loss=pos["stop_loss"],
                        target_price=pos["target_price"],
                        risk_amount_inr=pos["risk_amount_inr"],
                        exit_time=_as_py_datetime(ts),
                        exit_price=fc.filled_price,
                        status=status,
                        gross_pnl_inr=round(gross, 2),
                        costs_inr=total_costs,
                        net_pnl_inr=net,
                        is_paper=True,
                    )
                    self.session.add(trade)
                    trades.append(trade)

                    profit_locker.record_realized_pnl(net, timestamp=_as_py_datetime(ts))
                    realized_today += net
                    realized_this_week += net
                else:
                    still_open.append(pos)
            open_positions = still_open

            # --- 2. Generate a signal as-of this bar (no lookahead) and risk-check it ---
            if i + 1 < len(featured):
                signal = generate_signal(featured, symbol, i)
                if signal.is_actionable:
                    state = RiskState(
                        active_trading_capital_inr=profit_locker.active_trading_capital_inr,
                        open_positions=[OpenPosition(symbol=symbol, direction=p["direction"]) for p in open_positions],
                        trades_taken_today=trades_today_count,
                        realized_pnl_today_inr=realized_today,
                        realized_pnl_this_week_inr=realized_this_week,
                    )
                    decision = self.risk_engine.evaluate(signal, state)
                    if decision.approved:
                        next_open = float(featured["open"].iloc[i + 1])
                        is_buy_entry = signal.direction == SignalDirection.LONG
                        fc = fill_cost(next_open, decision.quantity, is_buy=is_buy_entry, settings=self.settings)
                        entry_ts = featured.index[i + 1]
                        open_positions.append(
                            {
                                "direction": signal.direction,
                                "entry_time": _as_py_datetime(entry_ts),
                                "entry_fill_price": fc.filled_price,
                                "entry_costs_inr": fc.total_cost_inr,
                                "quantity": decision.quantity,
                                "stop_loss": signal.stop_loss,
                                "target_price": signal.target_price,
                                "risk_amount_inr": decision.risk_amount_inr,
                                "expiry": signal.expiry,
                                "entry_bar_index": i + 1,
                            }
                        )
                        trades_today_count += 1

            # --- 3. Profit-lock cycle on the configured cadence ---
            marker = bar_day if self.settings.profit_lock_check_frequency == "daily" else bar_week
            if last_lock_marker is not None and marker != last_lock_marker:
                profit_locker.run_lock_cycle(timestamp=_as_py_datetime(ts))
            last_lock_marker = marker

        profit_locker.run_lock_cycle(timestamp=_as_py_datetime(featured.index[-1]))
        self.session.commit()

        in_sample_trades = [t for t in trades if t.exit_time < _as_py_datetime(split_ts)]
        out_of_sample_trades = [t for t in trades if t.exit_time >= _as_py_datetime(split_ts)]

        in_sample_metrics = compute_metrics(in_sample_trades, self.settings.active_trading_capital_inr)
        out_of_sample_metrics = compute_metrics(out_of_sample_trades, self.settings.active_trading_capital_inr)
        overall_metrics = compute_metrics(trades, self.settings.active_trading_capital_inr)

        run = BacktestRun(
            strategy_name=strategy_name,
            symbols=json.dumps([symbol]),
            start_date=str(featured.index[0].date()) if hasattr(featured.index[0], "date") else str(featured.index[0]),
            end_date=str(featured.index[-1].date()) if hasattr(featured.index[-1], "date") else str(featured.index[-1]),
            is_out_of_sample=True,
            parameters_json=json.dumps(self._parameters_snapshot(out_of_sample_fraction)),
            metrics_json=json.dumps(
                {
                    "in_sample": in_sample_metrics,
                    "out_of_sample": out_of_sample_metrics,
                    "overall": overall_metrics,
                    "split_date": str(split_ts),
                }
            ),
        )
        self.session.add(run)
        self.session.commit()

        return BacktestResult(
            backtest_run_id=run.id,
            in_sample_metrics=in_sample_metrics,
            out_of_sample_metrics=out_of_sample_metrics,
            overall_metrics=overall_metrics,
            split_date=str(split_ts),
            trades=trades,
            in_sample_trades=in_sample_trades,
            out_of_sample_trades=out_of_sample_trades,
        )

    def _parameters_snapshot(self, out_of_sample_fraction: float) -> dict:
        """Full config snapshot for this run, so results are reproducible
        (spec section 8/14: reproducible backtests with versioned
        parameters)."""
        return {
            "risk_per_trade_pct": self.settings.risk_per_trade_pct,
            "daily_loss_ceiling_inr": self.settings.daily_loss_ceiling_inr,
            "weekly_loss_ceiling_inr": self.settings.weekly_loss_ceiling_inr,
            "max_open_positions": self.settings.max_open_positions,
            "max_trades_per_day": self.settings.max_trades_per_day,
            "profit_lock_fraction": self.settings.profit_lock_fraction,
            "profit_lock_check_frequency": self.settings.profit_lock_check_frequency,
            "brokerage_intraday_pct": self.settings.brokerage_intraday_pct,
            "brokerage_intraday_cap_inr": self.settings.brokerage_intraday_cap_inr,
            "stt_sell_pct": self.settings.stt_sell_pct,
            "slippage_bps": self.settings.slippage_bps,
            "active_trading_capital_inr": self.settings.active_trading_capital_inr,
            "out_of_sample_fraction": out_of_sample_fraction,
        }
