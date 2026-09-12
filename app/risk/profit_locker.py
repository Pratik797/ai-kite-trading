"""
Profit Locker (spec section 2 "Profit Locker" / section 7 Profit-Locking).

Tracks three separate balances — protected capital, active trading capital,
and locked profit — and appends a CapitalLedgerEntry row (append-only, never
updated/deleted) every time any of them changes, so the full history is
auditable per spec section 11.

Critical invariant: only REALIZED P&L (from a closed trade) may ever reach
`record_realized_pnl`. Unrealized mark-to-market P&L is never treated as
protected/lockable cash (spec section 7 is explicit about this) — callers
must not pass open-position marks into this class.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from app.config import Settings, settings as default_settings
from app.db.models import CapitalLedgerEntry


class ProfitLocker:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or default_settings
        self.protected_capital_inr = self.settings.protected_capital_inr
        self.active_trading_capital_inr = self.settings.active_trading_capital_inr
        self.locked_profit_inr = 0.0
        self._realized_profit_since_last_lock_inr = 0.0
        self._seed_initial_ledger_entry()

    def _seed_initial_ledger_entry(self) -> None:
        self._append_ledger_entry("initial_split")

    def record_realized_pnl(self, net_pnl_inr: float, *, timestamp: dt.datetime | None = None) -> None:
        """Apply a closed trade's net P&L (positive or negative) to active
        trading capital, and — if it was a gain — accumulate it toward the
        next profit-lock cycle."""
        self.active_trading_capital_inr = round(self.active_trading_capital_inr + net_pnl_inr, 2)
        if net_pnl_inr > 0:
            self._realized_profit_since_last_lock_inr += net_pnl_inr
        self._append_ledger_entry("trade_settled", timestamp=timestamp)

    def run_lock_cycle(self, *, timestamp: dt.datetime | None = None) -> float:
        """Lock `profit_lock_fraction` of profit realized since the last
        cycle. Returns the amount locked (0.0 if there was nothing new to
        lock). Safe to call on every bar/day — it is a no-op when there has
        been no new realized profit."""
        pending = self._realized_profit_since_last_lock_inr
        self._realized_profit_since_last_lock_inr = 0.0

        if pending <= 0:
            return 0.0

        amount_to_lock = round(pending * self.settings.profit_lock_fraction, 2)
        if amount_to_lock <= 0:
            return 0.0

        self.active_trading_capital_inr = round(self.active_trading_capital_inr - amount_to_lock, 2)
        self.locked_profit_inr = round(self.locked_profit_inr + amount_to_lock, 2)
        self._append_ledger_entry("profit_lock_cycle", timestamp=timestamp)
        return amount_to_lock

    def _append_ledger_entry(self, reason: str, *, timestamp: dt.datetime | None = None) -> None:
        entry = CapitalLedgerEntry(
            protected_capital_inr=self.protected_capital_inr,
            active_trading_capital_inr=self.active_trading_capital_inr,
            locked_profit_inr=self.locked_profit_inr,
            reason=reason,
        )
        if timestamp is not None:
            entry.timestamp = timestamp
        self.session.add(entry)
        self.session.commit()
