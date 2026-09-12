from __future__ import annotations

from app.backtest.research import _passes_three_metric_rule, _pick_strongest


def test_passes_rule_requires_all_three_metrics_positive():
    assert _passes_three_metric_rule(
        {"num_trades": 10, "profit_factor": 1.2, "expectancy_inr": 1.0, "expectancy_r": 0.05}
    )


def test_fails_rule_when_expectancy_r_is_negative_even_if_others_are_positive():
    assert not _passes_three_metric_rule(
        {"num_trades": 10, "profit_factor": 1.2, "expectancy_inr": 1.0, "expectancy_r": -0.01}
    )


def test_fails_rule_when_profit_factor_below_one():
    assert not _passes_three_metric_rule(
        {"num_trades": 10, "profit_factor": 0.9, "expectancy_inr": 1.0, "expectancy_r": 0.05}
    )


def test_fails_rule_when_zero_trades():
    assert not _passes_three_metric_rule(
        {"num_trades": 0, "profit_factor": None, "expectancy_inr": 0.0, "expectancy_r": 0.0}
    )


def test_infinite_profit_factor_counts_as_passing():
    assert _passes_three_metric_rule(
        {"num_trades": 5, "profit_factor": float("inf"), "expectancy_inr": 2.0, "expectancy_r": 0.1}
    )


def test_pick_strongest_prefers_higher_profit_factor():
    metrics = {
        "A": {"profit_factor": 1.1, "expectancy_r": 0.02, "expectancy_inr": 0.5},
        "B": {"profit_factor": 1.5, "expectancy_r": 0.01, "expectancy_inr": 0.3},
    }
    winner, reason = _pick_strongest(["A", "B"], metrics)
    assert winner == "B"
    assert "B" in reason


def test_pick_strongest_tiebreaks_on_expectancy_r_when_profit_factor_ties():
    metrics = {
        "A": {"profit_factor": 1.2, "expectancy_r": 0.01, "expectancy_inr": 0.9},
        "B": {"profit_factor": 1.2, "expectancy_r": 0.05, "expectancy_inr": 0.2},
    }
    winner, _ = _pick_strongest(["A", "B"], metrics)
    assert winner == "B"
