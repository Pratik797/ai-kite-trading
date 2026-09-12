"""
Basket backtest orchestration.

This exists to answer a specific question the single-symbol backtests
(RELIANCE.NS: 0 trades; SUZLON.NS: 26 trades, near-breakeven) couldn't:
is a result specific to one symbol, or does it hold across a real basket
of liquid NSE names spanning a wide price range?

IMPORTANT METHODOLOGY NOTE: each tradeable symbol is backtested
INDEPENDENTLY, each starting from its own fresh ₹1,000 active capital (the
same single-symbol app.backtest.engine.BacktestEngine used everywhere else
in this project). This is NOT a shared-capital, simultaneous multi-symbol
portfolio simulation — that would require a materially different engine
with one shared risk/capital state arbitrating between concurrently
competing signals across symbols, which hasn't been built. Pooling
independent single-symbol runs is sufficient to answer "does this rule set
show an edge across many symbols" — the question actually asked — without
pretending to model a real shared-capital account trading a basket at once.

Metrics that only make sense against a single account's equity curve
(return %, drawdown %, final equity) are reported per-symbol only. Metrics
that pool correctly across independent trade sequences (win rate, profit
factor, expectancy, longest losing streak) are also reported as a combined,
pooled-trade aggregate across every tradeable symbol.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.backtest.benchmark import buy_and_hold_return_pct
from app.backtest.data import load_historical
from app.backtest.engine import BacktestEngine, BacktestResult
from app.backtest.metrics import compute_metrics
from app.backtest.screener import ScreenResult, screen_symbol
from app.config import Settings, settings as default_settings
from app.db.models import Trade

# A mix of Nifty50 large-caps (high price -> expected to fail the screener
# at Rs 1,000 active capital, same reason RELIANCE.NS produced zero trades)
# and lower-priced, liquid mid/large-cap names spanning a wide price range
# (expected to be more likely to actually size a position).
DEFAULT_BASKET: list[str] = [
    # Large-cap Nifty50
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
    "HINDUNILVR.NS", "ITC.NS", "SBIN.NS", "BHARTIARTL.NS", "KOTAKBANK.NS",
    "LT.NS", "MARUTI.NS",
    # Lower-priced liquid names
    "SUZLON.NS", "IDEA.NS", "YESBANK.NS", "TATAPOWER.NS", "IRFC.NS",
    "PNB.NS", "NHPC.NS", "NBCC.NS", "ONGC.NS", "COALINDIA.NS", "GAIL.NS",
    "NTPC.NS", "BANKBARODA.NS", "IDFCFIRSTB.NS", "FEDERALBNK.NS",
    "TATASTEEL.NS",
]


@dataclass
class SymbolOutcome:
    symbol: str
    screen: ScreenResult | None = None
    data_error: str | None = None
    backtest: BacktestResult | None = None
    buy_hold_full_pct: float | None = None
    buy_hold_in_sample_pct: float | None = None
    buy_hold_out_of_sample_pct: float | None = None


@dataclass
class BasketResult:
    outcomes: list[SymbolOutcome] = field(default_factory=list)
    tradeable_symbols: list[str] = field(default_factory=list)
    untradeable_symbols: list[str] = field(default_factory=list)
    data_unavailable_symbols: list[str] = field(default_factory=list)
    combined_in_sample_metrics: dict = field(default_factory=dict)
    combined_out_of_sample_metrics: dict = field(default_factory=dict)
    combined_overall_metrics: dict = field(default_factory=dict)
    avg_symbol_return_pct_overall: float = 0.0
    avg_symbol_return_pct_out_of_sample: float = 0.0
    avg_buy_hold_return_pct_overall: float = 0.0
    avg_buy_hold_return_pct_out_of_sample: float = 0.0


def run_basket(
    session: Session,
    symbols: list[str],
    start: str,
    end: str,
    *,
    settings: Settings | None = None,
    out_of_sample_fraction: float = 0.3,
) -> BasketResult:
    s = settings or default_settings
    engine = BacktestEngine(session, s)
    outcomes: list[SymbolOutcome] = []

    for symbol in symbols:
        try:
            df = load_historical(symbol, start, end)
        except Exception as exc:
            # One symbol's data/network failure must not kill the whole basket run.
            outcomes.append(SymbolOutcome(symbol=symbol, data_error=str(exc)))
            continue

        if len(df) < 60:
            outcomes.append(SymbolOutcome(symbol=symbol, data_error=f"only {len(df)} bars returned, too short"))
            continue

        screen = screen_symbol(symbol, df, s)
        outcome = SymbolOutcome(symbol=symbol, screen=screen)

        split_idx = int(len(df) * (1 - out_of_sample_fraction))
        outcome.buy_hold_full_pct = buy_and_hold_return_pct(df)
        outcome.buy_hold_in_sample_pct = buy_and_hold_return_pct(df, 0, max(split_idx - 1, 0))
        outcome.buy_hold_out_of_sample_pct = buy_and_hold_return_pct(df, split_idx, len(df) - 1)

        if screen.is_tradeable:
            outcome.backtest = engine.run(symbol, df, out_of_sample_fraction=out_of_sample_fraction)

        outcomes.append(outcome)

    tradeable = [o.symbol for o in outcomes if o.screen and o.screen.is_tradeable]
    untradeable = [o.symbol for o in outcomes if o.screen and not o.screen.is_tradeable]
    data_unavailable = [o.symbol for o in outcomes if o.data_error]

    pooled_in_sample: list[Trade] = []
    pooled_out_of_sample: list[Trade] = []
    pooled_overall: list[Trade] = []
    per_symbol_returns: list[float] = []
    per_symbol_oos_returns: list[float] = []
    per_symbol_buy_hold: list[float] = []
    per_symbol_buy_hold_oos: list[float] = []

    for o in outcomes:
        if o.backtest is None:
            continue
        pooled_in_sample.extend(o.backtest.in_sample_trades)
        pooled_out_of_sample.extend(o.backtest.out_of_sample_trades)
        pooled_overall.extend(o.backtest.trades)
        per_symbol_returns.append(o.backtest.overall_metrics["total_return_pct"])
        per_symbol_oos_returns.append(o.backtest.out_of_sample_metrics["total_return_pct"])
        if o.buy_hold_full_pct is not None:
            per_symbol_buy_hold.append(o.buy_hold_full_pct)
        if o.buy_hold_out_of_sample_pct is not None:
            per_symbol_buy_hold_oos.append(o.buy_hold_out_of_sample_pct)

    nominal_capital = s.active_trading_capital_inr

    def _avg(values: list[float]) -> float:
        return round(sum(values) / len(values), 3) if values else 0.0

    return BasketResult(
        outcomes=outcomes,
        tradeable_symbols=tradeable,
        untradeable_symbols=untradeable,
        data_unavailable_symbols=data_unavailable,
        combined_in_sample_metrics=compute_metrics(pooled_in_sample, nominal_capital),
        combined_out_of_sample_metrics=compute_metrics(pooled_out_of_sample, nominal_capital),
        combined_overall_metrics=compute_metrics(pooled_overall, nominal_capital),
        avg_symbol_return_pct_overall=_avg(per_symbol_returns),
        avg_symbol_return_pct_out_of_sample=_avg(per_symbol_oos_returns),
        avg_buy_hold_return_pct_overall=_avg(per_symbol_buy_hold),
        avg_buy_hold_return_pct_out_of_sample=_avg(per_symbol_buy_hold_oos),
    )
