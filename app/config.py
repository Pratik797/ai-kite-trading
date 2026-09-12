"""
Central configuration. Every value here is loaded from environment variables
(or a local .env file, never committed) — nothing sensitive is hardcoded.

Security baseline (spec section 11):
- No secrets in source control.
- No broker passwords ever stored (Kite uses an OAuth-style login flow that
  yields a short-lived access token, handled in the Phase 2/9 broker adapter —
  not implemented in this pass).
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Credentials (unused until Phase 2/5/9; left blank is fine) ---
    kite_api_key: str = ""
    kite_api_secret: str = ""
    kite_access_token: str = ""
    anthropic_api_key: str = ""

    # --- Database ---
    database_url: str = "sqlite:///./trading.db"

    # --- Capital / risk profile (spec section 6) ---
    total_capital_inr: float = 5000.0
    protected_capital_inr: float = 4000.0
    active_trading_capital_inr: float = 1000.0
    risk_per_trade_pct: float = 1.5          # 1-2% of ACTIVE capital per spec
    daily_loss_ceiling_inr: float = 50.0
    weekly_loss_ceiling_inr: float = 150.0    # configurable; conservative default (~3x daily)
    max_open_positions: int = 2
    max_trades_per_day: int = 4

    # --- Profit locking (spec section 7) ---
    profit_lock_fraction: float = 0.5         # fraction of newly realized profit locked each cycle
    profit_lock_check_frequency: str = "daily"  # "daily" | "weekly"

    # --- Backtesting cost assumptions (spec section 8, approximating Zerodha) ---
    brokerage_flat_inr: float = 0.0           # Zerodha equity delivery brokerage is typically ₹0
    brokerage_intraday_pct: float = 0.03      # ~0.03% or ₹20/executed order, whichever is lower (simplified here)
    brokerage_intraday_cap_inr: float = 20.0
    stt_sell_pct: float = 0.025               # Securities Transaction Tax on sell side (intraday approx.)
    slippage_bps: float = 5.0                 # 5 basis points assumed slippage per fill

    def validate_capital_split(self) -> None:
        if abs((self.protected_capital_inr + self.active_trading_capital_inr) - self.total_capital_inr) > 0.01:
            raise ValueError(
                "protected_capital_inr + active_trading_capital_inr must equal total_capital_inr"
            )


settings = Settings()
settings.validate_capital_split()
