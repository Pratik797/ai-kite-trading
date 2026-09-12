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
- **Screener** (`app/backtest/screener.py`) — checks whether a symbol is
  even tradeable at the current active capital / risk-per-trade % before
  backtesting it, using the median ATR-based stop distance.
- **Buy-and-hold benchmark** (`app/backtest/benchmark.py`) — the honest
  baseline the strategy needs to beat, not an absolute number in isolation.
- **Basket backtest** (`app/backtest/basket.py`) — screens and backtests a
  whole basket of symbols at once and pools the results, so a finding isn't
  a one-symbol fluke (see results below).
- **CLI** (`app/cli.py`) — `backtest` for one symbol, `basket-backtest` for
  a whole basket; both print and save a report.
- **Tests** (`tests/`) — 56 tests covering indicators, the baseline
  strategy (including a dedicated no-lookahead check), every risk-engine
  rejection rule and its position-sizing math, the profit locker, the cost
  model, the metrics calculations, the screener, the buy-and-hold
  benchmark, and an end-to-end engine smoke test.

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

All 56 tests pass as of this pass. Indicators, strategy, risk engine, profit
locker, cost model, metrics, screener, and buy-and-hold benchmark are each
independently unit-tested; the backtest engine also has an end-to-end
wiring test.

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

### Single-symbol results (superseded by the basket test below)

The first run of this pass only tested two symbols, and raised an obvious
question — was the result specific to those symbols?

**RELIANCE.NS (2019–2024): 0 trades.** At ₹1,000 active capital and 1.5%
risk per trade (a ₹15 risk budget), a stock priced ₹500–1,700 with
ATR-based stop distances of ₹15–30 sizes to **zero shares** every single
time the strategy fired a signal — the risk engine correctly refusing an
unsizeable trade, not a bug.

**SUZLON.NS (2019–2024, price range ~₹1.6–₹85): 26 trades**, roughly
break-even (profit factor 1.03 overall, negative out-of-sample expectancy).

### Basket screening + backtest — the real answer

To find out whether that was a structural account-size problem or a
one-symbol fluke, run:

```bash
python -m app.cli basket-backtest --start 2019-01-01 --end 2024-12-31
```

This screens a ~28-symbol basket (12 Nifty50 large-caps + 16 lower-priced
liquid names spanning ~₹1.6 to ~₹10,600) for tradeability at the current
active capital/risk-per-trade, backtests every symbol that passes, pools
the results, and benchmarks against buy-and-hold. **Methodology note:**
each symbol is backtested independently with its own fresh ₹1,000 — this
pools independent single-symbol runs, it is *not* a simulation of one
shared-capital account trading all 18 symbols at once (that would need a
different, portfolio-level engine, not built in this pass).

**Screener result: only 18 of 28 symbols are tradeable at ₹1,000 capital.**
Every large-cap that failed did so for the same reason as RELIANCE.NS —
ATR-based stop distances of ₹20–260 against a ₹15 risk budget round to
zero shares (RELIANCE, TCS, HDFCBANK, INFY, ICICIBANK, HINDUNILVR, SBIN,
BHARTIARTL, LT, MARUTI all failed; only KOTAKBANK and ITC among the
large-caps scraped through). **This confirms the account-size problem is
structural, not symbol-specific** — most of the Nifty50 is simply
untradeable under these risk rules at ₹1,000 active capital.

**Combined (pooled-trade) metrics across all 18 tradeable symbols, 2019–2024:**

| Metric | In-sample | Out-of-sample | Overall |
|---|---|---|---|
| Trades | 486 | 197 | 683 |
| Win rate % | 47.12 | 50.25 | 48.02 |
| Profit factor | 0.969 | 1.022 | 0.982 |
| Expectancy (₹/trade) | -0.10 | 0.05 | -0.05 |
| Expectancy (R) | -0.005 | -0.009 | -0.006 |
| Longest losing streak | 8 | 9 | 9 |

Average per-symbol return (equal-weighted, not a shared-capital portfolio):
**-0.21% overall, +0.06% out-of-sample.** Average buy-and-hold return on the
same symbols over the same windows: **+214.2% overall, +101.0%
out-of-sample.**

**Honest verdict: no consistent edge.** In-sample is net negative (profit
factor 0.969, expectancy -₹0.10/trade). Out-of-sample looks marginally
positive on profit factor (1.022) and rupee expectancy (+₹0.05/trade) but
is **negative on R-expectancy (-0.009)** — these don't agree with each
other, which is a sign of noise, not edge. The overall pooled profit factor
across everything is 0.982 — below breakeven. Individual symbols swing
wildly in both directions (NBCC +3.64 profit factor, KOTAKBANK 0.33) with
no obvious pattern distinguishing winners from losers, consistent with
random dispersion around a roughly-zero true edge rather than a real
signal some symbols have and others don't.

This is not specific to SUZLON.NS or any single symbol — across a real
18-symbol basket, after realistic costs, **the current rule set shows no
real edge.** It also captured essentially none of the enormous
buy-and-hold upside available over this (unusually strong) period, which
is expected for a strategy that only holds short-dated directional
positions — but is worth naming plainly rather than only reporting the
strategy's return in isolation.

**Do not deploy this rule set live. Do not spend money on Kite Connect
based on this result.** The full per-symbol and per-basket JSON is saved to
`reports/basket_backtest_<start>_<end>.json` for further analysis.

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
  backtest/                data.py, costs.py, engine.py, metrics.py,
                            screener.py, benchmark.py, basket.py
  cli.py                    typer CLI entrypoint
tests/                       56 tests
data_cache/                  yfinance CSV cache (gitignored)
reports/                      backtest JSON reports (gitignored)
```
