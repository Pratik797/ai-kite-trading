# AI Kite Trading — Foundation & Backtesting (Phase 1/3/6/7/8-partial)

This is the **deterministic, non-AI core** of a single-user Indian equity
algo-trading system, built per `AI_Kite_Trading_Production_Project_Specification.pdf`.
It exists to answer one question before any money touches a live broker
connection: **does a simple, rules-based strategy show any real,
cost-inclusive edge on historical data?**

**Profitability is a hypothesis being tested here, not a promised feature.**
Nothing in this codebase should be read as a guarantee of future returns —
see the honest results below.

## What's built in this pass

- **Config** (`app/config.py`) — all settings from environment variables /
  `.env` (never committed), including the ₹4,000 protected / ₹1,000 active
  capital split, risk limits, and cost assumptions.
- **Database models** (`app/db/`) — SQLAlchemy models for an append-only
  capital ledger, signals, trades, versioned backtest runs, and an
  immutable trade journal table. SQLite for local dev (per spec section 3);
  point `DATABASE_URL` at Postgres for production without code changes.
- **Feature engine** (`app/features/indicators.py`) — EMA, RSI, ATR, VWAP,
  rolling support/resistance, and an explainable (non-ML) market-regime
  classifier (trending up/down/range-bound).
- **Baseline strategy** (`app/strategy/`) — a deterministic EMA-crossover +
  RSI + VWAP + regime rule set. Outputs LONG/SHORT/**NO_TRADE** with
  entry/stop/target/confidence/reasons. No lookahead: a signal at bar N
  only ever sees data through bar N.
- **Risk engine** (`app/risk/engine.py`) — independently validates every
  signal against position sizing (1–2% of *active* capital), daily/weekly
  loss ceilings, max open positions, max trades/day, mandatory stop-loss,
  no averaging down, and no leverage. It can reject a signal regardless of
  its confidence score.
- **Profit locker** (`app/risk/profit_locker.py`) — tracks protected
  capital, active trading capital, and locked profit as separate balances,
  with every change appended to an audit-trail ledger. Locks a configurable
  fraction of *realized* profit only — unrealized P&L is never touched.
- **Backtest engine** (`app/backtest/`) — bar-by-bar replay with realistic
  Zerodha-like costs (brokerage, STT, slippage), an in-sample/out-of-sample
  split, and a full metrics report.
- **CLI** (`app/cli.py`) — run a real backtest and print/save the report.
- **Tests** (`tests/`) — 48 tests covering indicators, the baseline
  strategy (including a dedicated no-lookahead check), every risk-engine
  rejection rule and its position-sizing math, the profit locker, the
  cost model, the metrics calculations, and an end-to-end engine smoke test.

**Not built in this pass** (see `ROADMAP.md`): Kite Connect broker adapter,
Claude/Anthropic AI Analysis adapter, news ingestion, frontend dashboard,
paper/live trading loop, kill switch, live execution.

## Setup

```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1        # Windows PowerShell
pip install -r requirements.txt
```

Copy `.env.example` to `.env` if you want to override any defaults (not
required for backtesting — everything has a sane default and no
credentials are needed for this pass).

## Running the tests

```bash
python -m pytest -v
```

All 48 tests pass as of this pass. Indicators, strategy, risk engine, profit
locker, cost model, and metrics are each independently unit-tested; the
backtest engine also has an end-to-end wiring test.

## Running a backtest

```bash
python -m app.cli backtest --symbol SUZLON.NS --start 2019-01-01 --end 2024-12-31
```

`--symbol` takes a Yahoo-style NSE ticker (`.NS` suffix). Historical data
comes from the free `yfinance` package — **this is a substitute for
backtesting only, not a production/live data feed.** It's cached locally
under `data_cache/` (gitignored).

A JSON report is written to `reports/` (gitignored) and a `BacktestRun` row
with the full versioned parameter snapshot is saved to the database, so
every run is reproducible.

### Actual results from this pass

Two real runs are worth calling out, because they're an honest finding, not
a cherry-picked "it works" demo:

**RELIANCE.NS (2019–2024): 0 trades.** At ₹1,000 active capital and 1.5%
risk per trade (a ₹15 risk budget), a stock priced ₹500–1,700 with
ATR-based stop distances of ₹15–30 sizes to **zero shares** every single
time the strategy fired a signal. This is the risk engine working
correctly — it's telling you the account is too small to trade this stock
under these rules, not a bug.

**SUZLON.NS (2019–2024, price range ~₹1.6–₹85): 26 trades**, sized
correctly:

| Metric | In-sample | Out-of-sample | Overall |
|---|---|---|---|
| Trades | 14 | 12 | 26 |
| Total return % | 1.64 | -1.375 | 0.265 |
| Max drawdown % | 1.883 | 2.297 | 2.86 |
| Win rate % | 42.86 | 50.0 | 46.15 |
| Expectancy (₹/trade) | 1.17 | -1.15 | 0.10 |
| Profit factor | 1.429 | 0.705 | 1.031 |
| Final active equity (₹) | 1016.40 | 986.25 | 1002.65 |

**Read this honestly: the baseline strategy is approximately break-even,
after realistic costs, on this symbol over this period** — a profit factor
of 1.03 overall and a negative out-of-sample expectancy is not an edge, it's
noise. That is exactly the outcome this backtesting pass exists to surface
before spending money on live infrastructure. A different symbol, a
different date range, or added AI/news context in a later phase might
change this — but nothing here currently justifies live deployment.

## An honest note on capital size vs. Kite Connect's cost

At ₹1,000 active trading capital, **even a genuinely strong backtested
strategy will produce small absolute rupee returns** — a 20% annual edge on
₹1,000 is ₹200/year. Zerodha's Kite Connect API currently costs **₹500/month**
(₹6,000/year) for live market data and order execution. That fixed cost
alone would overwhelm any plausible return this account size could produce.

**Recommendation: don't pay for Kite Connect or deploy live until:**
1. A backtest (on this or a better-suited symbol/universe) shows a real,
   cost-inclusive, out-of-sample edge — not just an in-sample number, and
2. Capital has grown enough that the ₹500/month fee is a small fraction of
   expected returns, not a dominant cost.

See `ROADMAP.md` for what's needed to get there.

## Repository layout

```
app/
  config.py            settings from env vars
  db/                   SQLAlchemy models + session
  features/             indicators.py — EMA/RSI/ATR/VWAP/regime
  strategy/              signals.py, baseline.py — deterministic strategy
  risk/                   engine.py, profit_locker.py
  backtest/                data.py, costs.py, engine.py, metrics.py
  cli.py                    typer CLI entrypoint
tests/                       48 tests
data_cache/                  yfinance CSV cache (gitignored)
reports/                      backtest JSON reports (gitignored)
```
