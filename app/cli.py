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

from app.backtest.data import load_historical
from app.backtest.engine import BacktestEngine
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


if __name__ == "__main__":
    app()
