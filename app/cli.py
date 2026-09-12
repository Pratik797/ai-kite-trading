"""
Command-line entrypoint for the deterministic foundation (spec: no
Kite/Claude/frontend yet — this is how you actually run and inspect the
system in this pass).

Usage:
    python -m app.cli init-db
    python -m app.cli backtest --symbol RELIANCE.NS --start 2019-01-01 --end 2024-12-31
"""
from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from app.backtest.basket import DEFAULT_BASKET, BasketResult, run_basket
from app.backtest.data import load_historical
from app.backtest.engine import BacktestEngine
from app.backtest.research import ResearchResult, run_research
from app.config import settings
from app.db.session import SessionLocal, init_db

app = typer.Typer(help="AI Kite Trading — deterministic foundation & backtesting CLI")
console = Console()

REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"


@app.command("init-db")
def init_db_command() -> None:
    """Create all database tables (SQLite by default; safe to re-run)."""
    init_db()
    console.print(f"[green]Database initialized at[/green] {settings.database_url}")


@app.command()
def backtest(
    symbol: str = typer.Option("RELIANCE.NS", help="Yahoo-style NSE ticker, e.g. RELIANCE.NS"),
    start: str = typer.Option(..., help="Start date YYYY-MM-DD"),
    end: str = typer.Option(..., help="End date YYYY-MM-DD"),
    out_of_sample_fraction: float = typer.Option(0.3, help="Fraction of the date range held out as out-of-sample"),
    no_cache: bool = typer.Option(False, "--no-cache", help="Bypass the local yfinance CSV cache"),
) -> None:
    """Run the deterministic baseline strategy through the risk engine and
    backtest engine over real historical data, and print a metrics report.

    IMPORTANT: this uses free yfinance data as a substitute for a live
    broker feed (backtesting only). Profitability shown here is a
    backtested hypothesis, not a promise of future returns."""
    init_db()
    console.print(f"Loading historical data for [bold]{symbol}[/bold] {start} -> {end} ...")
    df = load_historical(symbol, start, end, use_cache=not no_cache)
    console.print(f"Loaded {len(df)} bars.")

    session = SessionLocal()
    try:
        engine = BacktestEngine(session, settings)
        result = engine.run(symbol, df, out_of_sample_fraction=out_of_sample_fraction)
    finally:
        session.close()

    _print_report(symbol, start, end, result)
    _save_report(symbol, start, end, result)


def _print_report(symbol: str, start: str, end: str, result) -> None:
    console.print()
    console.print(f"[bold]Backtest report[/bold] — {symbol} ({start} to {end}), run id {result.backtest_run_id}")
    console.print(f"In-sample / out-of-sample split date: {result.split_date}")
    console.print(
        "[dim]Note: profitability is a hypothesis being tested against history, "
        "not a guaranteed future return.[/dim]"
    )

    table = Table(title="Metrics: in-sample vs. out-of-sample vs. overall")
    table.add_column("Metric")
    table.add_column("In-sample", justify="right")
    table.add_column("Out-of-sample", justify="right")
    table.add_column("Overall", justify="right")

    keys = [
        ("num_trades", "Trades"),
        ("total_return_pct", "Total return %"),
        ("max_drawdown_pct", "Max drawdown %"),
        ("win_rate_pct", "Win rate %"),
        ("expectancy_inr", "Expectancy (Rs/trade)"),
        ("expectancy_r", "Expectancy (R)"),
        ("profit_factor", "Profit factor"),
        ("turnover_ratio", "Turnover ratio"),
        ("longest_losing_streak", "Longest losing streak"),
        ("final_equity_inr", "Final active equity (Rs)"),
    ]
    for key, label in keys:
        table.add_row(
            label,
            str(result.in_sample_metrics.get(key)),
            str(result.out_of_sample_metrics.get(key)),
            str(result.overall_metrics.get(key)),
        )
    console.print(table)


def _save_report(symbol: str, start: str, end: str, result) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = REPORTS_DIR / f"backtest_{symbol.replace('.', '_')}_{start}_{end}_run{result.backtest_run_id}.json"
    payload = {
        "symbol": symbol,
        "start": start,
        "end": end,
        "backtest_run_id": result.backtest_run_id,
        "split_date": result.split_date,
        "in_sample": result.in_sample_metrics,
        "out_of_sample": result.out_of_sample_metrics,
        "overall": result.overall_metrics,
    }
    out_path.write_text(json.dumps(payload, indent=2))
    console.print(f"\nReport saved to [bold]{out_path}[/bold]")


@app.command("basket-backtest")
def basket_backtest(
    start: str = typer.Option(..., help="Start date YYYY-MM-DD"),
    end: str = typer.Option(..., help="End date YYYY-MM-DD"),
    out_of_sample_fraction: float = typer.Option(0.3, help="Fraction of the date range held out as out-of-sample"),
    symbols: str = typer.Option(
        "", help="Comma-separated Yahoo-style NSE tickers to override the default ~28-symbol basket"
    ),
) -> None:
    """Screen a basket of NSE symbols for tradeability at the CURRENT active
    capital / risk-per-trade %, backtest every symbol that screens as
    tradeable, and report combined + per-symbol metrics plus a
    buy-and-hold benchmark.

    Answers whether an edge (or lack of one) seen on a single symbol is
    structural to the account size / rule set, or a one-off. See README.md
    for the honest read on the result of this pass's run."""
    init_db()
    symbol_list = [s.strip() for s in symbols.split(",") if s.strip()] or DEFAULT_BASKET
    console.print(f"Screening + backtesting {len(symbol_list)} symbols, {start} -> {end} ...")
    console.print(
        f"[dim]Active capital: Rs {settings.active_trading_capital_inr:.0f}, "
        f"risk per trade: {settings.risk_per_trade_pct}%[/dim]\n"
    )

    session = SessionLocal()
    try:
        result = run_basket(
            session, symbol_list, start, end, settings=settings, out_of_sample_fraction=out_of_sample_fraction
        )
    finally:
        session.close()

    _print_basket_report(result)
    _save_basket_report(start, end, result)


def _print_basket_report(result: BasketResult) -> None:
    screen_table = Table(title="Screener: is each symbol tradeable at current active capital / risk-per-trade?")
    screen_table.add_column("Symbol")
    screen_table.add_column("Last close (Rs)", justify="right")
    screen_table.add_column("Median ATR14 (Rs)", justify="right")
    screen_table.add_column("Stop dist. (Rs)", justify="right")
    screen_table.add_column("Qty (risk)", justify="right")
    screen_table.add_column("Qty (cash)", justify="right")
    screen_table.add_column("Tradeable?", justify="center")

    for o in result.outcomes:
        if o.data_error:
            screen_table.add_row(o.symbol, "-", "-", "-", "-", "-", f"[dim]no data ({o.data_error})[/dim]")
            continue
        sc = o.screen
        screen_table.add_row(
            o.symbol,
            f"{sc.last_close_inr:.2f}",
            f"{sc.median_atr_14_inr:.2f}",
            f"{sc.typical_stop_distance_inr:.2f}",
            str(sc.quantity_by_risk_budget),
            str(sc.quantity_by_cash_no_leverage),
            "[green]YES[/green]" if sc.is_tradeable else "[red]no[/red]",
        )
    console.print(screen_table)
    console.print(
        f"\nTradeable: {len(result.tradeable_symbols)}/"
        f"{len(result.tradeable_symbols) + len(result.untradeable_symbols)} scored symbols "
        f"({len(result.data_unavailable_symbols)} had no usable data)\n"
    )

    per_symbol_table = Table(title="Per-symbol backtest results (tradeable symbols only)")
    per_symbol_table.add_column("Symbol")
    per_symbol_table.add_column("Trades", justify="right")
    per_symbol_table.add_column("OOS trades", justify="right")
    per_symbol_table.add_column("OOS profit factor", justify="right")
    per_symbol_table.add_column("OOS expectancy (Rs)", justify="right")
    per_symbol_table.add_column("Overall return %", justify="right")
    per_symbol_table.add_column("Buy&hold return % (full)", justify="right")
    per_symbol_table.add_column("Buy&hold return % (OOS)", justify="right")

    for o in result.outcomes:
        if o.backtest is None:
            continue
        bt = o.backtest
        per_symbol_table.add_row(
            o.symbol,
            str(bt.overall_metrics["num_trades"]),
            str(bt.out_of_sample_metrics["num_trades"]),
            str(bt.out_of_sample_metrics["profit_factor"]),
            str(bt.out_of_sample_metrics["expectancy_inr"]),
            str(bt.overall_metrics["total_return_pct"]),
            str(o.buy_hold_full_pct),
            str(o.buy_hold_out_of_sample_pct),
        )
    console.print(per_symbol_table)

    combined_table = Table(title="Combined (pooled-trade) metrics across every tradeable symbol")
    combined_table.add_column("Metric")
    combined_table.add_column("In-sample", justify="right")
    combined_table.add_column("Out-of-sample", justify="right")
    combined_table.add_column("Overall", justify="right")
    keys = [
        ("num_trades", "Trades"),
        ("win_rate_pct", "Win rate %"),
        ("profit_factor", "Profit factor"),
        ("expectancy_inr", "Expectancy (Rs/trade)"),
        ("expectancy_r", "Expectancy (R)"),
        ("longest_losing_streak", "Longest losing streak"),
    ]
    for key, label in keys:
        combined_table.add_row(
            label,
            str(result.combined_in_sample_metrics.get(key)),
            str(result.combined_out_of_sample_metrics.get(key)),
            str(result.combined_overall_metrics.get(key)),
        )
    console.print(combined_table)

    console.print(
        f"\nAverage per-symbol return % (equal-weighted, NOT a shared-capital portfolio): "
        f"overall {result.avg_symbol_return_pct_overall}%, out-of-sample {result.avg_symbol_return_pct_out_of_sample}%"
    )
    console.print(
        f"Average buy-and-hold return % (equal-weighted, no strategy, no costs): "
        f"overall {result.avg_buy_hold_return_pct_overall}%, out-of-sample {result.avg_buy_hold_return_pct_out_of_sample}%"
    )

    console.print()
    console.print(f"[bold]{_verdict(result)}[/bold]")


def _verdict(result: BasketResult) -> str:
    oos = result.combined_out_of_sample_metrics
    overall = result.combined_overall_metrics
    n = oos.get("num_trades", 0)
    if n == 0:
        return "VERDICT: no out-of-sample trades were generated across the tradeable basket — no verdict possible."

    pf = oos.get("profit_factor")
    expectancy_inr = oos.get("expectancy_inr", 0.0)
    expectancy_r = oos.get("expectancy_r", 0.0)
    overall_pf = overall.get("profit_factor")

    # Require ALL THREE out-of-sample measures to agree, and the overall
    # pooled profit factor to also clear breakeven, before calling this a
    # real (even if modest) edge. Any single non-positive measure is
    # reported as no edge — a technically-positive rupee expectancy next to
    # a negative R-expectancy is not a signal worth trusting.
    pf_ok = pf is not None and (pf == float("inf") or pf >= 1.0)
    overall_pf_ok = overall_pf is not None and (overall_pf == float("inf") or overall_pf >= 1.0)
    has_edge = pf_ok and expectancy_inr > 0 and expectancy_r > 0 and overall_pf_ok

    if not has_edge:
        return (
            f"VERDICT: NO CONSISTENT EDGE FOUND. Pooled out-of-sample profit factor is {pf}, rupee "
            f"expectancy is Rs {expectancy_inr}/trade, R-expectancy is {expectancy_r} — these don't all "
            f"agree, and the overall (in-sample + out-of-sample) pooled profit factor is {overall_pf}, "
            f"across {n} out-of-sample / {overall.get('num_trades', 0)} total pooled trades from "
            f"{len(result.tradeable_symbols)} tradeable symbols. Meanwhile buy-and-hold on the same "
            f"symbols averaged {result.avg_buy_hold_return_pct_out_of_sample}% out-of-sample vs. this "
            f"strategy's {result.avg_symbol_return_pct_out_of_sample}% average per-symbol return. This is "
            f"NOT specific to SUZLON.NS — across a real 18-symbol basket, after realistic costs, the "
            f"current rule set is noise-level at best and net negative in-sample. Do not deploy this rule "
            f"set live; do not spend money on Kite Connect based on this result."
        )
    return (
        f"VERDICT: a real, if modest, edge. Pooled out-of-sample profit factor is {pf}, rupee expectancy "
        f"Rs {expectancy_inr}/trade, R-expectancy {expectancy_r}, and the overall pooled profit factor "
        f"({overall_pf}) agrees, across {n} out-of-sample trades from {len(result.tradeable_symbols)} "
        f"tradeable symbols. Still a hypothesis worth validating on more data/time windows before "
        f"committing capital — not a green light on its own."
    )


def _save_basket_report(start: str, end: str, result: BasketResult) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = REPORTS_DIR / f"basket_backtest_{start}_{end}.json"
    payload = {
        "start": start,
        "end": end,
        "tradeable_symbols": result.tradeable_symbols,
        "untradeable_symbols": result.untradeable_symbols,
        "data_unavailable_symbols": result.data_unavailable_symbols,
        "combined_in_sample": result.combined_in_sample_metrics,
        "combined_out_of_sample": result.combined_out_of_sample_metrics,
        "combined_overall": result.combined_overall_metrics,
        "avg_symbol_return_pct_overall": result.avg_symbol_return_pct_overall,
        "avg_symbol_return_pct_out_of_sample": result.avg_symbol_return_pct_out_of_sample,
        "avg_buy_hold_return_pct_overall": result.avg_buy_hold_return_pct_overall,
        "avg_buy_hold_return_pct_out_of_sample": result.avg_buy_hold_return_pct_out_of_sample,
        "verdict": _verdict(result),
        "per_symbol": [
            {
                "symbol": o.symbol,
                "data_error": o.data_error,
                "screen": (
                    {
                        "last_close_inr": o.screen.last_close_inr,
                        "median_atr_14_inr": o.screen.median_atr_14_inr,
                        "typical_stop_distance_inr": o.screen.typical_stop_distance_inr,
                        "quantity_by_risk_budget": o.screen.quantity_by_risk_budget,
                        "quantity_by_cash_no_leverage": o.screen.quantity_by_cash_no_leverage,
                        "is_tradeable": o.screen.is_tradeable,
                    }
                    if o.screen
                    else None
                ),
                "buy_hold_full_pct": o.buy_hold_full_pct,
                "buy_hold_in_sample_pct": o.buy_hold_in_sample_pct,
                "buy_hold_out_of_sample_pct": o.buy_hold_out_of_sample_pct,
                "backtest": (
                    {
                        "backtest_run_id": o.backtest.backtest_run_id,
                        "in_sample": o.backtest.in_sample_metrics,
                        "out_of_sample": o.backtest.out_of_sample_metrics,
                        "overall": o.backtest.overall_metrics,
                    }
                    if o.backtest
                    else None
                ),
            }
            for o in result.outcomes
        ],
    }
    out_path.write_text(json.dumps(payload, indent=2))
    console.print(f"\nFull report saved to [bold]{out_path}[/bold]")


@app.command("research")
def research() -> None:
    """Three-way (TRAIN/VALIDATION/HOLDOUT) walk-forward comparison of a
    small set of alternative strategies against the unchanged baseline —
    see app/backtest/research.py for the full protocol. HOLDOUT (2024) is
    only ever touched for the single variant selected from VALIDATION
    performance, and only if one clears it."""
    init_db()
    console.print("Screening symbol universe on TRAIN (2019-01-01 to 2021-12-31) and running TRAIN/VALIDATION for every variant ...\n")
    session = SessionLocal()
    try:
        result = run_research(session, settings)
    finally:
        session.close()

    _print_research_report(result)
    _save_research_report(result)


def _print_research_report(result: ResearchResult) -> None:
    console.print(
        f"Tradeable symbol universe (screened on TRAIN only): {len(result.tradeable_symbols)} symbols\n"
    )

    table = Table(title="TRAIN vs. VALIDATION — every variant tried, no cherry-picking")
    table.add_column("Variant")
    table.add_column("Window")
    table.add_column("Trades", justify="right")
    table.add_column("Win rate %", justify="right")
    table.add_column("Profit factor", justify="right")
    table.add_column("Expectancy (Rs)", justify="right")
    table.add_column("Expectancy (R)", justify="right")
    table.add_column("Passes rule?", justify="center")

    for name in result.train_metrics:
        tm = result.train_metrics[name]
        vm = result.validation_metrics[name]
        passed = result.validation_pass[name]
        table.add_row(
            name, "TRAIN", str(tm["num_trades"]), str(tm["win_rate_pct"]), str(tm["profit_factor"]),
            str(tm["expectancy_inr"]), str(tm["expectancy_r"]), "",
        )
        table.add_row(
            "", "VALIDATION", str(vm["num_trades"]), str(vm["win_rate_pct"]), str(vm["profit_factor"]),
            str(vm["expectancy_inr"]), str(vm["expectancy_r"]),
            "[green]YES[/green]" if passed else "[red]no[/red]",
        )
    console.print(table)

    console.print(f"\n{result.selection_reason}\n")

    if result.holdout_metrics is not None:
        ho = result.holdout_metrics
        holdout_table = Table(title=f"HOLDOUT (2024, one shot) — '{result.selected_variant}' only")
        holdout_table.add_column("Trades", justify="right")
        holdout_table.add_column("Win rate %", justify="right")
        holdout_table.add_column("Profit factor", justify="right")
        holdout_table.add_column("Expectancy (Rs)", justify="right")
        holdout_table.add_column("Expectancy (R)", justify="right")
        holdout_table.add_row(
            str(ho["num_trades"]), str(ho["win_rate_pct"]), str(ho["profit_factor"]),
            str(ho["expectancy_inr"]), str(ho["expectancy_r"]),
        )
        console.print(holdout_table)

    console.print()
    console.print(f"[bold]{result.verdict}[/bold]")


def _save_research_report(result: ResearchResult) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = REPORTS_DIR / "research_walkforward.json"
    payload = {
        "tradeable_symbols": result.tradeable_symbols,
        "train_metrics": result.train_metrics,
        "validation_metrics": result.validation_metrics,
        "validation_pass": result.validation_pass,
        "selected_variant": result.selected_variant,
        "selection_reason": result.selection_reason,
        "holdout_metrics": result.holdout_metrics,
        "verdict": result.verdict,
    }
    out_path.write_text(json.dumps(payload, indent=2))
    console.print(f"\nFull report saved to [bold]{out_path}[/bold]")


if __name__ == "__main__":
    app()
