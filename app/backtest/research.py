"""
Three-way (TRAIN / VALIDATION / HOLDOUT) walk-forward comparison of a
small, well-motivated set of alternative strategies against the existing
EMA-crossover baseline — NOT a parameter sweep of the baseline's own
constants. See README.md's "Strategy variant walk-forward test" section
for the write-up and the honest result.

Methodology, deliberately strict, because trying enough variants against
the same test data eventually produces something that looks good by
chance:

  TRAIN      2019-01-01 .. 2021-12-31  -- look at this freely while building
  VALIDATION 2022-01-01 .. 2023-12-31  -- run each finished variant ONCE
  HOLDOUT    2024-01-01 .. 2024-12-31  -- touched AT MOST ONCE, only for the
             single variant selected from VALIDATION performance, only
             after that selection is locked in from validation alone.

If nothing clears validation, HOLDOUT data is never even loaded for any
variant — Phase 1 below only ever requests data through VALIDATION_END;
2024 bars are fetched at all only in Phase 2, and only for the winner.

Symbol universe: re-screened using ONLY the TRAIN window (not the full
2019-2024 history the earlier basket-backtest used), so the "is this
symbol tradeable" decision doesn't leak information from
validation/holdout into a decision that should only depend on TRAIN.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import pandas as pd
from sqlalchemy.orm import Session

from app.backtest.basket import DEFAULT_BASKET
from app.backtest.data import load_historical
from app.backtest.engine import BacktestEngine, SignalFn
from app.backtest.metrics import compute_metrics
from app.backtest.screener import screen_symbol
from app.config import Settings, settings as default_settings
from app.db.models import Trade
from app.strategy.baseline import generate_signal as baseline_generate_signal
from app.strategy.variants import (
    MEAN_REVERSION_STRATEGY_NAME,
    REGIME_GATED_TREND_STRATEGY_NAME,
    generate_signal_mean_reversion,
    generate_signal_regime_gated_trend,
)

TRAIN_START = "2019-01-01"
TRAIN_END = "2021-12-31"
VALIDATION_START = "2022-01-01"
VALIDATION_END = "2023-12-31"
HOLDOUT_START = "2024-01-01"
HOLDOUT_END = "2024-12-31"

BASELINE_REFERENCE_NAME = "baseline_ema_rsi_atr (reference, unchanged)"

VARIANTS: dict[str, SignalFn] = {
    BASELINE_REFERENCE_NAME: baseline_generate_signal,
    MEAN_REVERSION_STRATEGY_NAME: generate_signal_mean_reversion,
    REGIME_GATED_TREND_STRATEGY_NAME: generate_signal_regime_gated_trend,
}


@dataclass
class ResearchResult:
    tradeable_symbols: list[str] = field(default_factory=list)
    train_metrics: dict[str, dict] = field(default_factory=dict)
    validation_metrics: dict[str, dict] = field(default_factory=dict)
    validation_pass: dict[str, bool] = field(default_factory=dict)
    selected_variant: str | None = None
    selection_reason: str = ""
    holdout_metrics: dict | None = None
    verdict: str = ""


def _passes_three_metric_rule(metrics: dict) -> bool:
    """Same standard as the basket-backtest CLI verdict: profit factor,
    rupee expectancy, and R-expectancy must ALL agree before anything
    counts as an edge."""
    if metrics.get("num_trades", 0) == 0:
        return False
    pf = metrics.get("profit_factor")
    pf_ok = pf is not None and (pf == float("inf") or pf >= 1.0)
    return pf_ok and metrics.get("expectancy_inr", 0.0) > 0 and metrics.get("expectancy_r", 0.0) > 0


def _screen_universe_on_train_only(settings: Settings) -> list[str]:
    """Re-screen tradeability using ONLY the TRAIN window, so this
    decision (unlike the earlier basket-backtest's whole-history screen)
    can't be informed by validation/holdout data."""
    tradeable = []
    for symbol in DEFAULT_BASKET:
        try:
            df = load_historical(symbol, TRAIN_START, TRAIN_END)
        except Exception:
            continue
        if len(df) < 60:
            continue
        screen = screen_symbol(symbol, df, settings)
        if screen.is_tradeable:
            tradeable.append(symbol)
    return tradeable


def _pooled_metrics_for_variant(
    session: Session,
    signal_fn: SignalFn,
    strategy_name: str,
    symbols: list[str],
    data_start: str,
    data_end: str,
    bucket_start: str,
    bucket_end: str,
    settings: Settings,
) -> dict:
    """Run `signal_fn` continuously over [data_start, data_end] for each
    symbol (so indicators are warmed up on real prior history, and a
    trend/regime signal isn't cut off mid-context), then pool only the
    CLOSED trades whose exit fell within [bucket_start, bucket_end]."""
    engine = BacktestEngine(session, settings)
    bucket_start_ts = pd.Timestamp(bucket_start)
    bucket_end_ts = pd.Timestamp(bucket_end) + pd.Timedelta(days=1)  # inclusive of the end date

    pooled: list[Trade] = []
    for symbol in symbols:
        try:
            df = load_historical(symbol, data_start, data_end)
        except Exception:
            continue
        if len(df) < 60:
            continue
        result = engine.run(symbol, df, out_of_sample_fraction=0.3, strategy_name=strategy_name, signal_fn=signal_fn)
        for t in result.trades:
            exit_ts = pd.Timestamp(t.exit_time)
            if bucket_start_ts <= exit_ts < bucket_end_ts:
                pooled.append(t)

    return compute_metrics(pooled, settings.active_trading_capital_inr)


def _pick_strongest(passing: list[str], validation_metrics: dict[str, dict]) -> tuple[str, str]:
    def _score(name: str) -> tuple[float, float, float]:
        m = validation_metrics[name]
        pf = m.get("profit_factor")
        pf_score = 1e9 if pf == float("inf") else (pf or 0.0)
        return (pf_score, m.get("expectancy_r", 0.0), m.get("expectancy_inr", 0.0))

    ranked = sorted(passing, key=_score, reverse=True)
    winner = ranked[0]
    reason = (
        f"{len(passing)} variants cleared validation ({', '.join(passing)}); "
        f"'{winner}' picked for the strongest agreement across profit factor, "
        f"R-expectancy, and rupee expectancy (in that priority order)."
    )
    return winner, reason


def run_research(session: Session, settings: Settings | None = None) -> ResearchResult:
    s = settings or default_settings
    result = ResearchResult()

    result.tradeable_symbols = _screen_universe_on_train_only(s)

    # --- Phase 1: TRAIN + VALIDATION only. HOLDOUT (2024) data is never
    # requested here for ANY variant. ---
    for name, signal_fn in VARIANTS.items():
        result.train_metrics[name] = _pooled_metrics_for_variant(
            session, signal_fn, name, result.tradeable_symbols,
            TRAIN_START, VALIDATION_END, TRAIN_START, TRAIN_END, s,
        )
        result.validation_metrics[name] = _pooled_metrics_for_variant(
            session, signal_fn, name, result.tradeable_symbols,
            TRAIN_START, VALIDATION_END, VALIDATION_START, VALIDATION_END, s,
        )
        result.validation_pass[name] = _passes_three_metric_rule(result.validation_metrics[name])

    passing = [name for name, ok in result.validation_pass.items() if ok]

    if not passing:
        result.selected_variant = None
        result.selection_reason = "No variant cleared validation (profit factor, rupee expectancy, and R-expectancy did not all agree for any variant)."
        result.holdout_metrics = None
        result.verdict = (
            "VERDICT: NO VARIANT SHOWS A VALIDATED EDGE. None of the tested variants — the unchanged "
            "baseline, mean-reversion, or regime-gated trend — passed the three-metric-agreement rule on "
            "the 2022-2023 validation window. Per the pre-committed protocol, the 2024 holdout year was "
            "never touched. Do not deploy any of these rule sets live."
        )
        return result

    if len(passing) == 1:
        winner = passing[0]
        result.selection_reason = f"Only '{winner}' cleared validation; selected by elimination."
    else:
        winner, reason = _pick_strongest(passing, result.validation_metrics)
        result.selection_reason = reason

    result.selected_variant = winner

    # --- Phase 2: HOLDOUT, only for the selected variant, only now. ---
    result.holdout_metrics = _pooled_metrics_for_variant(
        session, VARIANTS[winner], winner, result.tradeable_symbols,
        TRAIN_START, HOLDOUT_END, HOLDOUT_START, HOLDOUT_END, s,
    )

    holdout_ok = _passes_three_metric_rule(result.holdout_metrics)
    if holdout_ok:
        result.verdict = (
            f"VERDICT: '{winner}' cleared validation and ALSO held up on the one-shot 2024 holdout — "
            f"profit factor {result.holdout_metrics.get('profit_factor')}, rupee expectancy "
            f"Rs {result.holdout_metrics.get('expectancy_inr')}/trade, R-expectancy "
            f"{result.holdout_metrics.get('expectancy_r')}. This is the strongest evidence produced so far, "
            f"but it is still one holdout year on one variant — treat it as a promising hypothesis to keep "
            f"validating, not a green light to deploy capital."
        )
    else:
        result.verdict = (
            f"VERDICT: '{winner}' cleared validation but FAILED the one-shot 2024 holdout — profit factor "
            f"{result.holdout_metrics.get('profit_factor')}, rupee expectancy "
            f"Rs {result.holdout_metrics.get('expectancy_inr')}/trade, R-expectancy "
            f"{result.holdout_metrics.get('expectancy_r')}. This is exactly the outcome the three-way split "
            f"exists to catch — a result that looked promising on validation didn't generalize. No variant "
            f"tested in this pass shows a real edge. Do not deploy any of these rule sets live."
        )

    return result
