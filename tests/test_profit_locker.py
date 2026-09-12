from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db.models import Base, CapitalLedgerEntry
from app.risk.profit_locker import ProfitLocker


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _settings(**overrides) -> Settings:
    defaults = dict(protected_capital_inr=4000.0, active_trading_capital_inr=1000.0, profit_lock_fraction=0.5)
    defaults.update(overrides)
    return Settings(**defaults)


def test_seeds_an_initial_ledger_entry_on_construction(db_session):
    ProfitLocker(db_session, _settings())
    entries = db_session.query(CapitalLedgerEntry).all()
    assert len(entries) == 1
    assert entries[0].reason == "initial_split"
    assert entries[0].protected_capital_inr == 4000.0
    assert entries[0].active_trading_capital_inr == 1000.0


def test_lock_cycle_locks_configured_fraction_of_realized_profit(db_session):
    locker = ProfitLocker(db_session, _settings(profit_lock_fraction=0.5))
    locker.record_realized_pnl(100.0)
    locked = locker.run_lock_cycle()
    assert locked == 50.0
    assert locker.locked_profit_inr == 50.0
    assert locker.active_trading_capital_inr == 1000.0 + 100.0 - 50.0


def test_realized_losses_reduce_active_capital_but_are_never_locked(db_session):
    locker = ProfitLocker(db_session, _settings(profit_lock_fraction=0.5))
    locker.record_realized_pnl(-30.0)
    locked = locker.run_lock_cycle()
    assert locked == 0.0
    assert locker.locked_profit_inr == 0.0
    assert locker.active_trading_capital_inr == 1000.0 - 30.0


def test_lock_cycle_is_a_noop_when_nothing_new_to_lock(db_session):
    locker = ProfitLocker(db_session, _settings())
    assert locker.run_lock_cycle() == 0.0
    # Calling it again after a no-op shouldn't lock stale profit either.
    locker.record_realized_pnl(40.0)
    locker.run_lock_cycle()
    assert locker.run_lock_cycle() == 0.0


def test_mixed_wins_and_losses_only_locks_net_new_profit(db_session):
    locker = ProfitLocker(db_session, _settings(profit_lock_fraction=0.5))
    locker.record_realized_pnl(100.0)  # +100 profit accumulated
    locker.record_realized_pnl(-40.0)  # a loss does not add to the lockable pool
    locked = locker.run_lock_cycle()
    assert locked == 50.0  # 50% of the +100 win only, loss is not netted into the lock pool


def test_ledger_entries_are_append_only_and_ordered(db_session):
    locker = ProfitLocker(db_session, _settings(profit_lock_fraction=0.5))
    locker.record_realized_pnl(100.0)
    locker.run_lock_cycle()
    entries = db_session.query(CapitalLedgerEntry).order_by(CapitalLedgerEntry.id).all()
    assert [e.reason for e in entries] == ["initial_split", "trade_settled", "profit_lock_cycle"]
    # protected capital never changes in this pass — only active/locked move.
    assert all(e.protected_capital_inr == 4000.0 for e in entries)
