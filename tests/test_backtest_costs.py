from __future__ import annotations

from app.backtest.costs import apply_slippage, fill_cost
from app.config import Settings


def _settings(**overrides) -> Settings:
    defaults = dict(
        brokerage_flat_inr=0.0,
        brokerage_intraday_pct=0.03,
        brokerage_intraday_cap_inr=20.0,
        stt_sell_pct=0.025,
        slippage_bps=5.0,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def test_slippage_moves_against_the_trader_on_both_sides():
    settings = _settings(slippage_bps=10.0)
    buy_price = apply_slippage(100.0, is_buy=True, settings=settings)
    sell_price = apply_slippage(100.0, is_buy=False, settings=settings)
    assert buy_price > 100.0   # buys fill worse (higher)
    assert sell_price < 100.0  # sells fill worse (lower)
    assert abs(buy_price - 100.10) < 1e-9
    assert abs(sell_price - 99.90) < 1e-9


def test_brokerage_is_capped_for_large_turnover():
    settings = _settings(slippage_bps=0.0)
    fc = fill_cost(1000.0, 1000, is_buy=True, settings=settings)  # turnover = 1,000,000
    assert fc.brokerage_inr == settings.brokerage_intraday_cap_inr


def test_brokerage_is_percent_based_for_small_turnover():
    settings = _settings(slippage_bps=0.0)
    fc = fill_cost(100.0, 10, is_buy=True, settings=settings)  # turnover = 1000
    expected = 1000 * (settings.brokerage_intraday_pct / 100.0)
    assert abs(fc.brokerage_inr - expected) < 1e-9


def test_stt_applies_only_to_the_sell_leg():
    settings = _settings(slippage_bps=0.0)
    buy_fc = fill_cost(100.0, 10, is_buy=True, settings=settings)
    sell_fc = fill_cost(100.0, 10, is_buy=False, settings=settings)
    assert buy_fc.stt_inr == 0.0
    expected_stt = 1000 * (settings.stt_sell_pct / 100.0)
    assert abs(sell_fc.stt_inr - expected_stt) < 1e-9


def test_total_cost_is_brokerage_plus_stt():
    settings = _settings(slippage_bps=0.0)
    fc = fill_cost(100.0, 10, is_buy=False, settings=settings)
    assert abs(fc.total_cost_inr - (fc.brokerage_inr + fc.stt_inr)) < 1e-9
