"""
Realistic Zerodha-like transaction cost model for backtesting (spec section
8: "include brokerage, taxes/charges assumptions, slippage and realistic
fills"). This models intraday equity costs — the baseline strategy is
intraday-style (signals expire after ~1 day and positions are closed on
stop/target/EOD, never held with averaging).

All rates come from the constants already parameterized in app/config.py.
These are simplified backtesting ASSUMPTIONS, not a live cost feed — they
must be re-checked against Zerodha's current published tariff before any
live deployment (spec section 10), since brokerage/STT/exchange charges
change over time.

Components modeled per executed order:
  - Brokerage: intraday_pct of turnover, capped at brokerage_intraday_cap_inr.
  - STT: applied only on the SELL leg of a trade (whichever leg, entry or
    exit, is actually a sell — e.g. entering a SHORT sells first, so STT
    applies at entry for shorts and at exit for longs).
  - Slippage: applied against the trader on every fill — a buy fills higher
    than intended, a sell fills lower — modeled as a bps adjustment to the
    fill price itself, before any turnover-based cost above.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings, settings as default_settings


@dataclass
class FillCost:
    filled_price: float          # price actually achieved, after slippage
    brokerage_inr: float
    stt_inr: float
    total_cost_inr: float


def apply_slippage(intended_price: float, *, is_buy: bool, settings: Settings | None = None) -> float:
    """Realistic fill price after slippage. A buy fills higher than
    intended; a sell fills lower — slippage always works against the
    trader, never in their favor."""
    s = settings or default_settings
    adjustment = intended_price * (s.slippage_bps / 10_000.0)
    return intended_price + adjustment if is_buy else intended_price - adjustment


def _brokerage_inr(turnover_inr: float, settings: Settings) -> float:
    pct_based = turnover_inr * (settings.brokerage_intraday_pct / 100.0)
    return min(pct_based, settings.brokerage_intraday_cap_inr) + settings.brokerage_flat_inr


def fill_cost(intended_price: float, quantity: int, *, is_buy: bool, settings: Settings | None = None) -> FillCost:
    """Cost/fill-price for one executed order leg. `is_buy` describes the
    actual order side of THIS leg (True = buying shares, False = selling
    shares) — callers are responsible for mapping entry/exit direction to
    the correct order side (e.g. a LONG entry buys and its exit sells; a
    SHORT entry sells and its exit buys)."""
    s = settings or default_settings
    filled_price = apply_slippage(intended_price, is_buy=is_buy, settings=s)
    turnover = filled_price * quantity
    brokerage = _brokerage_inr(turnover, s)
    stt = 0.0 if is_buy else turnover * (s.stt_sell_pct / 100.0)
    return FillCost(
        filled_price=round(filled_price, 4),
        brokerage_inr=round(brokerage, 2),
        stt_inr=round(stt, 2),
        total_cost_inr=round(brokerage + stt, 2),
    )
