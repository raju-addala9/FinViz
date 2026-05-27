from __future__ import annotations

import time
import re
from datetime import date, datetime, timedelta
from html import unescape
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests


NASDAQ_EARNINGS_URL = "https://api.nasdaq.com/api/calendar/earnings"
ALPHAQUERY_EARNINGS_URL = "https://www.alphaquery.com/stock/{symbol}/earnings-history"
PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_EARNINGS_HISTORY_PATH = PROJECT_ROOT / "data" / "earnings_history.csv"
DEFAULT_PORTFOLIO_PATH = PROJECT_ROOT / "portfolio.csv"
PORTFOLIO_SYMBOL_COLUMNS = ["Symbol", "Ticker", "Ticker Symbol", "Holding Ticker", "Security Ticker"]
NASDAQ_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/market-activity/earnings",
}
WEB_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def clean_symbol(symbol: object) -> str:
    return str(symbol or "").strip().upper()


def default_start_date(years: int = 2) -> date:
    return date.today() - timedelta(days=365 * years)


def date_range(start_date: date, end_date: date) -> Iterable[date]:
    current = start_date
    while current <= end_date:
        yield current
        current += timedelta(days=1)


def read_cached_history(path: Path = DEFAULT_EARNINGS_HISTORY_PATH) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    if "Symbol" in df.columns:
        df["Symbol"] = df["Symbol"].map(clean_symbol)
    if "Earnings Date" in df.columns:
        df["Earnings Date"] = pd.to_datetime(df["Earnings Date"], errors="coerce").dt.date.astype(str)
    return df


def normalize_cache_frame(df: pd.DataFrame) -> pd.DataFrame:
    normalized = df.copy()
    for column in ["Symbol", "Earnings Date", "Time", "Fiscal Quarter Ending", "Cache Status"]:
        if column in normalized.columns:
            normalized[column] = normalized[column].fillna("").astype(str).str.strip()
    if "Symbol" in normalized.columns:
        normalized["Symbol"] = normalized["Symbol"].map(clean_symbol)
    return normalized


def cached_dates(path: Path = DEFAULT_EARNINGS_HISTORY_PATH) -> set[str]:
    df = read_cached_history(path)
    if df.empty or "Scraped Calendar Date" not in df.columns:
        return set()
    return set(df["Scraped Calendar Date"].dropna().astype(str))


def read_portfolio_symbols(path: Path = DEFAULT_PORTFOLIO_PATH) -> list[str]:
    if not path.exists():
        return []
    df = pd.read_csv(path)
    df.columns = [str(column).strip() for column in df.columns]
    symbol_column = next((column for column in PORTFOLIO_SYMBOL_COLUMNS if column in df.columns), "")
    if not symbol_column:
        return []
    symbols = [clean_symbol(symbol) for symbol in df[symbol_column].dropna().tolist()]
    return sorted({symbol for symbol in symbols if symbol})


def cached_symbols(path: Path = DEFAULT_EARNINGS_HISTORY_PATH, start_date: date | None = None) -> set[str]:
    df = read_cached_history(path)
    if df.empty or "Symbol" not in df.columns:
        return set()
    if start_date is not None and "Cache Checked At" in df.columns:
        checked = pd.to_datetime(df["Cache Checked At"], errors="coerce").dt.date
        df = df[checked >= start_date]
    return set(df["Symbol"].dropna().map(clean_symbol))


def normalize_nasdaq_rows(
    rows: list[dict],
    calendar_date: date,
    allowed_symbols: set[str] | None = None,
) -> list[dict]:
    normalized = []
    for row in rows:
        symbol = clean_symbol(row.get("symbol"))
        if not symbol:
            continue
        if allowed_symbols is not None and symbol not in allowed_symbols:
            continue
        normalized.append(
            {
                "Symbol": symbol,
                "Earnings Date": calendar_date.isoformat(),
                "Time": str(row.get("time") or "").strip(),
                "Company Name": str(row.get("name") or "").strip(),
                "EPS": str(row.get("eps") or "").strip(),
                "Surprise %": str(row.get("surprise") or "").strip(),
                "EPS Forecast": str(row.get("epsForecast") or "").strip(),
                "# Ests": str(row.get("noOfEsts") or "").strip(),
                "Fiscal Quarter Ending": str(row.get("fiscalQuarterEnding") or "").strip(),
                "Market Cap": str(row.get("marketCap") or "").strip(),
                "Source": "Nasdaq earnings calendar",
                "Scraped Calendar Date": calendar_date.isoformat(),
                "Cache Status": "FOUND",
                "Cache Checked At": date.today().isoformat(),
                "Fetched At": datetime.now().isoformat(timespec="seconds"),
            }
        )
    return normalized


def missing_symbol_rows(symbols: set[str]) -> list[dict]:
    checked_at = date.today().isoformat()
    return [
        {
            "Symbol": symbol,
            "Earnings Date": "",
            "Time": "",
            "Company Name": "",
            "EPS": "",
            "Surprise %": "",
            "EPS Forecast": "",
            "# Ests": "",
            "Fiscal Quarter Ending": "",
            "Market Cap": "",
            "Source": "Nasdaq earnings calendar",
            "Scraped Calendar Date": "",
            "Cache Status": "NO_EARNINGS_FOUND",
            "Cache Checked At": checked_at,
            "Fetched At": datetime.now().isoformat(timespec="seconds"),
        }
        for symbol in sorted(symbols)
    ]


def strip_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", "", value)
    return unescape(text).strip()


def normalize_symbol_for_web(symbol: str) -> str:
    return clean_symbol(symbol).replace("^", "").replace("/", ".")


def fetch_alphaquery_earnings_symbol(symbol: str, start_date: date, end_date: date) -> list[dict]:
    web_symbol = normalize_symbol_for_web(symbol)
    if not web_symbol:
        return []

    response = requests.get(
        ALPHAQUERY_EARNINGS_URL.format(symbol=web_symbol),
        headers=WEB_HEADERS,
        timeout=15,
    )
    response.raise_for_status()
    html = response.text
    if "Announcement Date" not in html or "Actual EPS" not in html:
        return []

    rows: list[dict] = []
    for raw_row in re.findall(r"<tr>(.*?)</tr>", html, flags=re.DOTALL | re.IGNORECASE):
        cells = [strip_html(cell) for cell in re.findall(r"<td[^>]*>(.*?)</td>", raw_row, flags=re.DOTALL | re.IGNORECASE)]
        if len(cells) < 4:
            continue
        announcement_date = pd.to_datetime(cells[0], errors="coerce")
        if pd.isna(announcement_date):
            continue
        event_date = announcement_date.date()
        if event_date < start_date or event_date > end_date:
            continue
        rows.append(
            {
                "Symbol": clean_symbol(symbol),
                "Earnings Date": event_date.isoformat(),
                "Time": "",
                "Company Name": "",
                "EPS": cells[3],
                "Surprise %": "",
                "EPS Forecast": cells[2],
                "# Ests": "",
                "Fiscal Quarter Ending": cells[1],
                "Market Cap": "",
                "Source": "AlphaQuery earnings history",
                "Scraped Calendar Date": "",
                "Cache Status": "FOUND",
                "Cache Checked At": date.today().isoformat(),
                "Fetched At": datetime.now().isoformat(timespec="seconds"),
            }
        )
    return rows


def fetch_nasdaq_earnings_day(calendar_date: date, allowed_symbols: set[str] | None = None) -> list[dict]:
    response = requests.get(
        NASDAQ_EARNINGS_URL,
        params={"date": calendar_date.isoformat()},
        headers=NASDAQ_HEADERS,
        timeout=12,
    )
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data") or {}
    rows = data.get("rows") or []
    return normalize_nasdaq_rows(rows, calendar_date, allowed_symbols)


def scrape_earnings_history(
    output_path: Path = DEFAULT_EARNINGS_HISTORY_PATH,
    start_date: date | None = None,
    end_date: date | None = None,
    force: bool = False,
    sleep_seconds: float = 0.08,
) -> tuple[pd.DataFrame, int, int]:
    start_date = start_date or default_start_date(2)
    end_date = end_date or date.today()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    existing = read_cached_history(output_path)
    already_scraped = set() if force else cached_dates(output_path)
    target_dates = [item for item in date_range(start_date, end_date) if item.isoformat() not in already_scraped]

    fetched_rows: list[dict] = []
    for index, calendar_date in enumerate(target_dates, start=1):
        print(f"[{index}/{len(target_dates)}] Fetching {calendar_date.isoformat()}")
        try:
            fetched_rows.extend(fetch_nasdaq_earnings_day(calendar_date))
        except Exception as exc:
            print(f"  warning: failed {calendar_date.isoformat()}: {exc}")
        if sleep_seconds:
            time.sleep(sleep_seconds)

    new_df = pd.DataFrame(fetched_rows)
    combined = pd.concat([existing, new_df], ignore_index=True) if not existing.empty else new_df
    if not combined.empty:
        combined = normalize_cache_frame(combined)
        combined = combined.drop_duplicates(
            subset=["Symbol", "Earnings Date", "Time", "Fiscal Quarter Ending"],
            keep="last",
        ).sort_values(["Earnings Date", "Symbol"])
    combined.to_csv(output_path, index=False)
    return combined, len(target_dates), len(fetched_rows)


def scrape_portfolio_earnings_history(
    symbols: list[str],
    output_path: Path = DEFAULT_EARNINGS_HISTORY_PATH,
    start_date: date | None = None,
    end_date: date | None = None,
    force: bool = False,
    sleep_seconds: float = 0.05,
) -> tuple[pd.DataFrame, int, int, list[str]]:
    start_date = start_date or default_start_date(2)
    end_date = end_date or date.today()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    target_symbols = {clean_symbol(symbol) for symbol in symbols if clean_symbol(symbol)}
    if not target_symbols:
        existing = read_cached_history(output_path)
        return existing, 0, 0, []

    existing = read_cached_history(output_path)
    existing_for_target = existing
    if not existing.empty and "Symbol" in existing.columns:
        existing_for_target = existing[existing["Symbol"].map(clean_symbol).isin(target_symbols)].copy()
    symbols_to_fetch = target_symbols if force else target_symbols - cached_symbols(output_path, start_date)
    if not symbols_to_fetch:
        return existing_for_target, 0, 0, []

    fetched_rows: list[dict] = []
    for index, symbol in enumerate(sorted(symbols_to_fetch), start=1):
        print(f"[{index}/{len(symbols_to_fetch)}] Fetching {symbol}", flush=True)
        try:
            symbol_rows = fetch_alphaquery_earnings_symbol(symbol, start_date, end_date)
            fetched_rows.extend(symbol_rows)
            if symbol_rows:
                print(f"  found {len(symbol_rows)} earnings rows", flush=True)
            else:
                print("  no earnings rows found", flush=True)
        except Exception as exc:
            print(f"  warning: failed {symbol}: {exc}", flush=True)
        if sleep_seconds:
            time.sleep(sleep_seconds)

    found_symbols = {clean_symbol(row.get("Symbol")) for row in fetched_rows}
    fetched_rows.extend(missing_symbol_rows(symbols_to_fetch - found_symbols))

    new_df = pd.DataFrame(fetched_rows)
    combined = pd.concat([existing, new_df], ignore_index=True) if not existing.empty else new_df
    if not combined.empty:
        combined = normalize_cache_frame(combined)
        combined = combined.drop_duplicates(
            subset=["Symbol", "Earnings Date", "Time", "Fiscal Quarter Ending", "Cache Status"],
            keep="last",
        )
        combined["Sort Date"] = pd.to_datetime(combined["Earnings Date"], errors="coerce")
        combined = combined.sort_values(["Symbol", "Sort Date"], na_position="last").drop(columns=["Sort Date"])
    combined.to_csv(output_path, index=False)
    return combined, len(symbols_to_fetch), len(fetched_rows), sorted(symbols_to_fetch)


def best_cached_earnings_event(symbol: str, path: Path = DEFAULT_EARNINGS_HISTORY_PATH) -> dict:
    df = read_cached_history(path)
    if df.empty or "Symbol" not in df.columns or "Earnings Date" not in df.columns:
        return {}

    symbol_df = df[df["Symbol"] == clean_symbol(symbol)].copy()
    if symbol_df.empty:
        return {}

    symbol_df["Parsed Date"] = pd.to_datetime(symbol_df["Earnings Date"], errors="coerce")
    symbol_df = symbol_df.dropna(subset=["Parsed Date"])
    if symbol_df.empty:
        return {}

    today = pd.Timestamp(date.today())
    previous = symbol_df[symbol_df["Parsed Date"] <= today].sort_values("Parsed Date", ascending=False)
    upcoming = symbol_df[symbol_df["Parsed Date"] > today].sort_values("Parsed Date", ascending=True)
    row = upcoming.head(1) if not upcoming.empty else previous.head(1)
    if row.empty:
        return {}

    record = row.iloc[0].to_dict()
    time_value = record.get("Time")
    source_value = record.get("Source")
    return {
        "ticker": record.get("Symbol"),
        "date": str(record.get("Earnings Date")),
        "time": "" if pd.isna(time_value) else str(time_value or "").strip(),
        "source": "Local earnings history" if pd.isna(source_value) else str(source_value or "Local earnings history"),
    }


def cached_earnings_events(symbol: str, path: Path = DEFAULT_EARNINGS_HISTORY_PATH) -> list[dict]:
    df = read_cached_history(path)
    if df.empty or "Symbol" not in df.columns or "Earnings Date" not in df.columns:
        return []

    symbol_df = df[df["Symbol"] == clean_symbol(symbol)].copy()
    if symbol_df.empty:
        return []

    symbol_df["Parsed Date"] = pd.to_datetime(symbol_df["Earnings Date"], errors="coerce")
    symbol_df = symbol_df.dropna(subset=["Parsed Date"]).sort_values("Parsed Date")
    events = []
    for _, record in symbol_df.iterrows():
        time_value = record.get("Time")
        source_value = record.get("Source")
        events.append(
            {
                "ticker": record.get("Symbol"),
                "date": str(record.get("Earnings Date")),
                "time": "" if pd.isna(time_value) else str(time_value or "").strip(),
                "source": "Local earnings history" if pd.isna(source_value) else str(source_value or "Local earnings history"),
            }
        )
    return events
