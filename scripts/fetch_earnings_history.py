#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

VENV_PYTHON = ROOT_DIR / ".venv" / "bin" / "python"
if VENV_PYTHON.exists() and not str(sys.executable).startswith(str(VENV_PYTHON.parent)):
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), *sys.argv])

from earnings_history import (
    DEFAULT_EARNINGS_HISTORY_PATH,
    DEFAULT_PORTFOLIO_PATH,
    read_portfolio_symbols,
    scrape_earnings_history,
    scrape_portfolio_earnings_history,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cache Nasdaq earnings-calendar history locally.")
    parser.add_argument("--years", type=int, default=2, help="Number of years to cache. Default: 2.")
    parser.add_argument("--start", default="", help="Start date YYYY-MM-DD. Overrides --years.")
    parser.add_argument("--end", default="", help="End date YYYY-MM-DD. Default: today.")
    parser.add_argument("--output", default=str(DEFAULT_EARNINGS_HISTORY_PATH), help="Output CSV path.")
    parser.add_argument("--portfolio", default=str(DEFAULT_PORTFOLIO_PATH), help="Portfolio CSV path.")
    parser.add_argument("--symbols", default="", help="Comma-separated symbols. Overrides --portfolio.")
    parser.add_argument("--all-market", action="store_true", help="Scrape all market earnings rows instead of portfolio symbols only.")
    parser.add_argument("--force", action="store_true", help="Re-fetch dates even when already cached.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    end_date = date.fromisoformat(args.end) if args.end else date.today()
    start_date = date.fromisoformat(args.start) if args.start else end_date - timedelta(days=365 * args.years)
    output_path = Path(args.output)

    print(f"Local cache: {output_path}")
    print(f"Range: {start_date.isoformat()} through {end_date.isoformat()}")
    print("Checking local cache first; only missing symbols/dates will be scraped.", flush=True)

    if args.all_market:
        print("Mode: all-market earnings calendar", flush=True)
        history_df, fetched_dates, fetched_rows = scrape_earnings_history(
            output_path=output_path,
            start_date=start_date,
            end_date=end_date,
            force=args.force,
        )
        fetched_symbols = []
    else:
        symbols = (
            [symbol.strip().upper() for symbol in args.symbols.split(",") if symbol.strip()]
            if args.symbols
            else read_portfolio_symbols(Path(args.portfolio))
        )
        print(f"Mode: portfolio symbols only ({len(symbols)} symbols)", flush=True)
        print(f"Symbols: {', '.join(symbols)}", flush=True)
        history_df, fetched_dates, fetched_rows, fetched_symbols = scrape_portfolio_earnings_history(
            symbols=symbols,
            output_path=output_path,
            start_date=start_date,
            end_date=end_date,
            force=args.force,
        )

    if fetched_symbols:
        print(f"Fetched symbols: {', '.join(fetched_symbols)}")
    elif not args.all_market:
        print("Fetched symbols: none; all portfolio symbols already cached.")
    print(f"Fetched dates: {fetched_dates}")
    print(f"Fetched rows: {fetched_rows}")
    print(f"Total cached rows: {len(history_df)}")
    print(f"Saved: {output_path}")


if __name__ == "__main__":
    main()
