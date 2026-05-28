from __future__ import annotations

import math
import os
import re
import zipfile
from datetime import date, datetime, timedelta
from html import unescape
from io import StringIO
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import quote
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st
import streamlit.components.v1 as components

from earnings_history import (
    DEFAULT_EARNINGS_HISTORY_PATH,
    best_cached_earnings_event,
    cached_earnings_events,
    read_cached_history,
    scrape_portfolio_earnings_history,
)

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_PORTFOLIO_PATH = PROJECT_ROOT / "portfolio.csv"
RAJU_COMPARE_PATH = Path("Raju.csv")
PADMAJA_COMPARE_PATH = Path("padmaja.csv")
RAJU_ACTIVITY_PATTERN = "Activity_Gan_Raju_*.xlsx"
PADMAJA_ACTIVITY_PATTERN = "Activity_Gan_Padmaja_Rollover_over_*6192_*.xlsx"
ADDED_TICKERS_PATH = Path("data/added_tickers.csv")
DEFAULT_EARNINGS_PATH = Path("earnings.csv")
POLYGON_AGGS_URL = "https://api.polygon.io/v2/aggs/ticker/{symbol}/range/{multiplier}/{timespan}/{from_date}/{to_date}"
POLYGON_GROUPED_AGGS_URL = "https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks/{date}"
POLYGON_PREV_CLOSE_URL = "https://api.polygon.io/v2/aggs/ticker/{symbol}/prev"
POLYGON_EARNINGS_URL = "https://api.polygon.io/benzinga/v1/earnings"
POLYGON_TICKER_DETAILS_URL = "https://api.polygon.io/v3/reference/tickers/{symbol}"
POLYGON_SNAPSHOT_URL = "https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers/{symbol}"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
FINVIZ_QUOTE_URL = "https://finviz.com/quote.ashx?t={symbol}&p=d"
MARKETBEAT_EARNINGS_URL = "https://www.marketbeat.com/stocks/{exchange}/{symbol}/earnings/"
STOOQ_QUOTES_URL = "https://stooq.com/q/l/"
PERIOD_OPTIONS = {
    "1D": {"days": 3, "multiplier": 5, "timespan": "minute"},
    "5D": {"days": 8, "multiplier": 30, "timespan": "minute"},
    "1M": {"days": 31, "multiplier": 1, "timespan": "day"},
    "6M": {"days": 183, "multiplier": 1, "timespan": "day"},
    "YTD": {"days": None, "multiplier": 1, "timespan": "day"},
    "1Y": {"days": 365, "multiplier": 1, "timespan": "day"},
    "5Y": {"days": 365 * 5, "multiplier": 1, "timespan": "week"},
    "MAX": {"days": 365 * 10, "multiplier": 1, "timespan": "month"},
}
PAGE_OPTIONS = ["Overview", "Charts", "Patterns", "Day Trade", "Compare", "Holdings", "Research Notes"]
MOVING_AVERAGE_WINDOWS = [10, 20, 50, 200]
NUMERIC_COLUMNS = [
    "Current Price",
    "Change",
    "Open",
    "High",
    "Low",
    "Volume",
    "Purchase Price",
    "Quantity",
    "Commission",
    "High Limit",
    "Low Limit",
    "Market Value",
    "Cost Basis",
    "Gain/Loss",
    "Gain/Loss %",
]
PORTFOLIO_COLUMN_ALIASES = {
    "Symbol": ["Ticker", "Ticker Symbol", "Holding Ticker", "Security Ticker", "Investment Ticker"],
    "Current Price": ["Price", "Market Price", "Last Price", "Last Close", "Close Price"],
    "Quantity": ["Shares", "Units", "Holding Quantity", "Share Quantity"],
    "Purchase Price": ["Cost Per Share", "Average Cost", "Avg Cost", "Cost Basis Per Share"],
    "Market Value": ["Value", "Market Value $", "Current Value"],
    "Cost Basis": ["Total Cost", "Book Value", "Cost"],
    "Gain/Loss": ["Gain Loss", "Unrealized Gain/Loss", "Unrealized Gain Loss"],
    "Comment": ["Notes", "Note", "Research Notes"],
}


st.set_page_config(
    page_title="Gan Portfolio Dashboard",
    page_icon="📈",
    layout="wide",
)


def clean_symbol(symbol: object) -> str:
    return str(symbol or "").strip().upper()


def polygon_symbol(symbol: str) -> str:
    cleaned = clean_symbol(symbol)
    if cleaned.startswith("^"):
        cleaned = cleaned[1:]
    return cleaned.replace("/", ".")


def yahoo_symbol(symbol: str) -> str:
    cleaned = clean_symbol(symbol)
    if cleaned == "^IXIC":
        return "^IXIC"
    return cleaned.replace(".", "-").replace("/", "-")


def configured_polygon_api_key(sidebar_api_key: str = "") -> str:
    if sidebar_api_key.strip():
        return sidebar_api_key.strip()
    env_key = os.getenv("POLYGON_API_KEY", "").strip()
    if env_key:
        return env_key
    try:
        return str(st.secrets.get("POLYGON_API_KEY", "")).strip()
    except Exception:
        return ""


def polygon_error_message(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code == 429:
        return "Polygon rate limit hit. Wait about a minute, then click Refresh local data or reload the chart."
    if status_code in {401, 403}:
        return "Polygon rejected this request for the current API plan/key."
    message = str(exc)
    if "apiKey=" in message:
        message = re.sub(r"apiKey=[^&\\s]+", "apiKey=***", message)
    return message


def observed_date(month: int, day: int, year: int) -> date:
    holiday = date(year, month, day)
    if holiday.weekday() == 5:
        return holiday - timedelta(days=1)
    if holiday.weekday() == 6:
        return holiday + timedelta(days=1)
    return holiday


def nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    current = date(year, month, 1)
    while current.weekday() != weekday:
        current += timedelta(days=1)
    return current + timedelta(days=7 * (occurrence - 1))


def last_weekday(year: int, month: int, weekday: int) -> date:
    current = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    while current.weekday() != weekday:
        current -= timedelta(days=1)
    return current


def easter_date(year: int) -> date:
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def us_market_holidays(year: int) -> set[date]:
    return {
        observed_date(1, 1, year),
        nth_weekday(year, 1, 0, 3),
        nth_weekday(year, 2, 0, 3),
        easter_date(year) - timedelta(days=2),
        last_weekday(year, 5, 0),
        observed_date(6, 19, year),
        observed_date(7, 4, year),
        nth_weekday(year, 9, 0, 1),
        nth_weekday(year, 11, 3, 4),
        observed_date(12, 25, year),
    }


def is_trading_day(day: date) -> bool:
    return day.weekday() < 5 and day not in us_market_holidays(day.year)


def previous_trading_day(day: date) -> date:
    current = day
    while not is_trading_day(current):
        current -= timedelta(days=1)
    return current


def add_trading_days(day: date, trading_days: int) -> date:
    step = 1 if trading_days >= 0 else -1
    remaining = abs(trading_days)
    current = day
    while remaining:
        current += timedelta(days=step)
        if is_trading_day(current):
            remaining -= 1
    return current


def market_holiday_rangebreak_values(start: date, end: date) -> list[datetime]:
    values = []
    current = start
    while current <= end:
        if current.weekday() < 5 and current in us_market_holidays(current.year):
            values.append(datetime.combine(current, datetime.min.time()))
        current += timedelta(days=1)
    return values


def apply_market_time_axis(fig: go.Figure, frame: pd.DataFrame, skip_closed_hours: bool = False) -> None:
    if frame.empty or "Datetime" not in frame.columns:
        return
    datetimes = pd.to_datetime(frame["Datetime"]).dropna()
    if datetimes.empty:
        return
    start = datetimes.min().date()
    end = datetimes.max().date()
    rangebreaks = [
        {"bounds": ["sat", "mon"]},
        {"values": market_holiday_rangebreak_values(start, end)},
    ]
    if skip_closed_hours:
        rangebreaks.append({"bounds": [16, 9.5], "pattern": "hour"})
    fig.update_xaxes(rangebreaks=rangebreaks)


def period_date_bounds(period_label: str) -> tuple[str, str]:
    today = previous_trading_day(date.today())
    option = PERIOD_OPTIONS[period_label]
    if period_label == "YTD":
        start = date(today.year, 1, 1)
    elif period_label == "1D":
        start = add_trading_days(today, -5)
    elif period_label == "5D":
        start = add_trading_days(today, -8)
    else:
        start = today - timedelta(days=int(option["days"]))
    return start.isoformat(), today.isoformat()


def apply_portfolio_column_aliases(df: pd.DataFrame) -> pd.DataFrame:
    renamed = df.copy()
    for canonical_column, aliases in PORTFOLIO_COLUMN_ALIASES.items():
        if canonical_column in renamed.columns:
            continue
        for alias in aliases:
            if alias in renamed.columns:
                renamed[canonical_column] = renamed[alias]
                break
    return renamed


def parse_numeric_series(series: pd.Series) -> pd.Series:
    cleaned = (
        series.astype(str)
        .str.replace("$", "", regex=False)
        .str.replace(",", "", regex=False)
        .str.replace("%", "", regex=False)
        .str.replace("(", "-", regex=False)
        .str.replace(")", "", regex=False)
        .str.strip()
    )
    return pd.to_numeric(cleaned, errors="coerce")


def read_portfolio(uploaded_file: object | None = None, path: Path = DEFAULT_PORTFOLIO_PATH) -> pd.DataFrame:
    if uploaded_file is not None:
        df = pd.read_csv(uploaded_file)
    elif path.exists():
        df = pd.read_csv(path)
    else:
        return pd.DataFrame()

    df.columns = [str(column).strip() for column in df.columns]
    df = apply_portfolio_column_aliases(df)
    if "Symbol" not in df.columns:
        return pd.DataFrame()

    df["Symbol"] = df["Symbol"].map(clean_symbol)
    df = df[df["Symbol"] != ""].copy()
    for column in NUMERIC_COLUMNS:
        if column in df.columns:
            df[column] = parse_numeric_series(df[column])

    if "Market Value" not in df.columns and "Current Price" in df.columns and "Quantity" in df.columns:
        df["Market Value"] = df["Current Price"].fillna(0) * df["Quantity"].fillna(0)
    elif "Market Value" not in df.columns:
        df["Market Value"] = 0.0

    if "Cost Basis" not in df.columns and {"Purchase Price", "Quantity"}.issubset(df.columns):
        df["Cost Basis"] = df["Purchase Price"].fillna(0) * df["Quantity"].fillna(0)

    if "Gain/Loss" not in df.columns and {"Market Value", "Cost Basis"}.issubset(df.columns):
        df["Gain/Loss"] = df["Market Value"].fillna(0) - df["Cost Basis"].fillna(0)

    if "Gain/Loss %" not in df.columns and {"Gain/Loss", "Cost Basis"}.issubset(df.columns):
        df["Gain/Loss %"] = df.apply(
            lambda row: (row["Gain/Loss"] / row["Cost Basis"] * 100) if row["Cost Basis"] else math.nan,
            axis=1,
        )
    elif {"Current Price", "Purchase Price", "Quantity"}.issubset(df.columns):
        df["Cost Basis"] = df["Purchase Price"].fillna(0) * df["Quantity"].fillna(0)
        df["Gain/Loss"] = df["Market Value"] - df["Cost Basis"]
        df["Gain/Loss %"] = df.apply(
            lambda row: (row["Gain/Loss"] / row["Cost Basis"] * 100) if row["Cost Basis"] else math.nan,
            axis=1,
        )
    else:
        if "Cost Basis" not in df.columns:
            df["Cost Basis"] = 0.0
        if "Gain/Loss" not in df.columns:
            df["Gain/Loss"] = 0.0
        if "Gain/Loss %" not in df.columns:
            df["Gain/Loss %"] = math.nan

    return df.reset_index(drop=True)


def format_file_age(path: Path) -> str:
    try:
        modified = datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return "missing"
    age = datetime.now() - modified
    if age.days:
        return f"{age.days}d old"
    hours = int(age.total_seconds() // 3600)
    if hours:
        return f"{hours}h old"
    minutes = max(0, int(age.total_seconds() // 60))
    return f"{minutes}m old"


def added_ticker_frame(symbols: list[str]) -> pd.DataFrame:
    rows = []
    for symbol in symbols:
        rows.append(
            {
                "Symbol": symbol,
                "Current Price": math.nan,
                "Change": math.nan,
                "Open": math.nan,
                "High": math.nan,
                "Low": math.nan,
                "Volume": math.nan,
                "Purchase Price": math.nan,
                "Quantity": 0.0,
                "Market Value": 0.0,
                "Cost Basis": 0.0,
                "Gain/Loss": 0.0,
                "Gain/Loss %": math.nan,
                "Comment": "Added ticker",
            }
        )
    return pd.DataFrame(rows)


def read_added_tickers(path: Path = ADDED_TICKERS_PATH) -> list[str]:
    if not path.exists():
        return []
    try:
        df = pd.read_csv(path)
    except Exception:
        return []
    if "Symbol" not in df.columns:
        return []
    return sorted({clean_symbol(symbol) for symbol in df["Symbol"].dropna() if clean_symbol(symbol)})


def save_added_ticker(symbol: str, path: Path = ADDED_TICKERS_PATH) -> None:
    cleaned_symbol = clean_symbol(symbol)
    if not cleaned_symbol:
        return
    symbols = set(read_added_tickers(path))
    symbols.add(cleaned_symbol)
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"Symbol": sorted(symbols)}).to_csv(path, index=False)


def ticker_exists(symbol: str, api_key: str) -> bool:
    if not api_key.strip():
        return False
    details = fetch_ticker_details(symbol, api_key)
    return bool(details.get("ticker") or details.get("name"))


def merge_added_tickers(portfolio_df: pd.DataFrame) -> pd.DataFrame:
    added_symbols = read_added_tickers()
    if not added_symbols:
        return portfolio_df
    existing_symbols = set(portfolio_df["Symbol"].dropna().astype(str)) if "Symbol" in portfolio_df.columns else set()
    missing_symbols = [symbol for symbol in added_symbols if symbol not in existing_symbols]
    if not missing_symbols:
        return portfolio_df
    added_df = added_ticker_frame(missing_symbols)
    if portfolio_df.empty:
        return added_df.reset_index(drop=True)
    return pd.concat([portfolio_df, added_df], ignore_index=True, sort=False).reset_index(drop=True)


def apply_live_ticker_prices(portfolio_df: pd.DataFrame, api_key: str) -> pd.DataFrame:
    if portfolio_df.empty or "Symbol" not in portfolio_df.columns:
        return portfolio_df

    live_df = portfolio_df.copy()
    symbols = sorted({clean_symbol(symbol) for symbol in live_df["Symbol"].dropna() if clean_symbol(symbol)})
    if not symbols:
        return live_df

    live_updates: Dict[str, Dict[str, object]] = {}
    live_updates.update(fetch_stooq_quotes(symbols))

    grouped_quotes: Dict[str, Dict[str, object]] = {}
    previous_quotes: Dict[str, Dict[str, object]] = {}
    market_date = previous_trading_day(date.today())
    if api_key.strip():
        try:
            grouped_quotes = fetch_grouped_daily_quotes(market_date.isoformat(), api_key)
            if not grouped_quotes and market_date == date.today():
                market_date = add_trading_days(market_date, -1)
                grouped_quotes = fetch_grouped_daily_quotes(market_date.isoformat(), api_key)
            previous_quotes = fetch_grouped_daily_quotes(add_trading_days(market_date, -1).isoformat(), api_key)
        except RuntimeError:
            grouped_quotes = {}
            previous_quotes = {}

    for symbol in symbols:
        if symbol in live_updates:
            continue
        row = grouped_quotes.get(polygon_symbol(symbol))
        if not row:
            continue
        previous_row = previous_quotes.get(polygon_symbol(symbol), {})
        latest_price = numeric_value(row.get("c"))
        previous_close = numeric_value(previous_row.get("c"))
        update: Dict[str, object] = {"Price Source": f"Polygon grouped {market_date.isoformat()}"}
        if not pd.isna(latest_price):
            update["Current Price"] = latest_price
        if not pd.isna(latest_price) and not pd.isna(previous_close):
            update["Change"] = latest_price - previous_close
            update["Previous Close"] = previous_close
        for source_key, target_column in [("o", "Open"), ("h", "High"), ("l", "Low"), ("v", "Volume")]:
            if row.get(source_key) is not None:
                update[target_column] = row.get(source_key)
        if len(update) > 1:
            live_updates[symbol] = update

    if not live_updates:
        return live_df

    live_df["Symbol"] = live_df["Symbol"].map(clean_symbol)
    stale_price_columns = [
        "Current Price",
        "Change",
        "Price Source",
        "Previous Close",
        "Open",
        "High",
        "Low",
        "Volume",
    ]
    for column in stale_price_columns:
        if column in live_df.columns:
            live_df[column] = pd.NA if column == "Price Source" else math.nan

    for symbol, update in live_updates.items():
        row_mask = live_df["Symbol"] == symbol
        for column, value in update.items():
            if column not in live_df.columns:
                live_df[column] = pd.NA if isinstance(value, str) else math.nan
            live_df.loc[row_mask, column] = value

    if {"Current Price", "Quantity"}.issubset(live_df.columns):
        live_df["Market Value"] = live_df["Current Price"].fillna(0) * live_df["Quantity"].fillna(0)
    if {"Purchase Price", "Quantity"}.issubset(live_df.columns):
        live_df["Cost Basis"] = live_df["Purchase Price"].fillna(0) * live_df["Quantity"].fillna(0)
    if {"Market Value", "Cost Basis"}.issubset(live_df.columns):
        live_df["Gain/Loss"] = live_df["Market Value"].fillna(0) - live_df["Cost Basis"].fillna(0)
        live_df["Gain/Loss %"] = live_df.apply(
            lambda row: (row["Gain/Loss"] / row["Cost Basis"] * 100) if row["Cost Basis"] else math.nan,
            axis=1,
        )

    return live_df


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    normalized = df.copy()
    normalized.columns = [str(column).strip() for column in normalized.columns]
    return normalized


def first_existing_value(row: pd.Series, candidates: list[str]) -> object:
    for candidate in candidates:
        if candidate in row and not pd.isna(row[candidate]):
            return row[candidate]
    return None


def parse_event_date(value: object) -> Optional[date]:
    if value is None or pd.isna(value):
        return None
    try:
        return pd.to_datetime(value).date()
    except Exception:
        return None


def event_from_manual_values(
    symbol: str,
    event_date: Optional[date],
    time_value: object = "",
    source: str = "Manual",
) -> Dict[str, object]:
    if event_date is None:
        return {}
    return {
        "ticker": polygon_symbol(symbol),
        "date": event_date.isoformat(),
        "time": str(time_value or "").strip(),
        "source": source,
    }


def choose_best_earnings_event(events: list[Dict[str, object]]) -> Dict[str, object]:
    today = date.today()
    dated_events = [
        (earnings_event_date(event), event)
        for event in events
        if earnings_event_date(event) is not None
    ]
    upcoming = sorted((item for item in dated_events if item[0] >= today), key=lambda item: item[0])
    if upcoming:
        return {"event": upcoming[0][1], "kind": "Next", "error": "", "source": upcoming[0][1].get("source", "Manual")}
    previous = sorted((item for item in dated_events if item[0] < today), key=lambda item: item[0], reverse=True)
    if previous:
        return {"event": previous[0][1], "kind": "Last", "error": "", "source": previous[0][1].get("source", "Manual")}
    return {"event": None, "kind": "", "error": "", "source": ""}


@st.cache_data(ttl=60, show_spinner=False)
def fetch_manual_earnings_data(symbol: str, path_text: str, path_mtime: float) -> Dict[str, object]:
    del path_mtime
    path = Path(path_text)
    if not path.exists():
        return {"event": None, "kind": "", "error": "", "source": ""}

    try:
        df = normalize_columns(pd.read_csv(path))
    except Exception as exc:
        return {"event": None, "kind": "", "error": f"Could not read {path}: {exc}", "source": "Manual"}

    if "Symbol" not in df.columns:
        return {"event": None, "kind": "", "error": f"{path} needs a Symbol column.", "source": "Manual"}

    symbol_rows = df[df["Symbol"].map(clean_symbol) == clean_symbol(symbol)].copy()
    if symbol_rows.empty:
        return {"event": None, "kind": "", "error": "", "source": ""}

    events = []
    for _, row in symbol_rows.iterrows():
        event_date = parse_event_date(
            first_existing_value(
                row,
                ["Earnings Date", "Earning Date", "Date", "Report Date", "report_date", "date"],
            )
        )
        event = event_from_manual_values(
            symbol=symbol,
            event_date=event_date,
            time_value=first_existing_value(row, ["Time", "time", "Time Of Day", "time_of_day"]),
            source=str(first_existing_value(row, ["Source", "source"]) or "Manual CSV"),
        )
        if event:
            events.append(event)

    result = choose_best_earnings_event(events)
    if result.get("event"):
        return result
    return {"event": None, "kind": "", "error": f"No valid earnings date found for {symbol} in {path}.", "source": "Manual"}


@st.cache_data(ttl=300, show_spinner=False)
def fetch_previous_close(symbol: str, api_key: str) -> float:
    encoded_symbol = quote(polygon_symbol(symbol), safe="")
    response = requests.get(
        POLYGON_PREV_CLOSE_URL.format(symbol=encoded_symbol),
        params={"adjusted": "true", "apiKey": api_key},
        timeout=20,
    )
    if response.status_code == 429:
        raise RuntimeError("Polygon rate limit hit. Wait about a minute, then refresh.")
    response.raise_for_status()
    payload = response.json()
    results = payload.get("results") or []
    if not results:
        return math.nan
    return float(results[0].get("c", math.nan))


def yahoo_chart_options(period_label: str) -> Dict[str, str]:
    return {
        "1D": {"range": "5d", "interval": "5m"},
        "5D": {"range": "5d", "interval": "30m"},
        "1M": {"range": "1mo", "interval": "1d"},
        "6M": {"range": "6mo", "interval": "1d"},
        "YTD": {"range": "ytd", "interval": "1d"},
        "1Y": {"range": "1y", "interval": "1d"},
        "5Y": {"range": "5y", "interval": "1wk"},
        "MAX": {"range": "10y", "interval": "1mo"},
    }.get(period_label, {"range": "6mo", "interval": "1d"})


def previous_close_from_prior_chart_date(frame: pd.DataFrame) -> float:
    if frame.empty or not {"Datetime", "Close"}.issubset(frame.columns):
        return math.nan
    close_df = frame.dropna(subset=["Datetime", "Close"]).copy()
    if close_df.empty:
        return math.nan
    close_df["Chart Date"] = pd.to_datetime(close_df["Datetime"]).dt.date
    latest_chart_date = close_df["Chart Date"].max()
    prior_dates = sorted(date_value for date_value in close_df["Chart Date"].unique() if date_value < latest_chart_date)
    if not prior_dates:
        return math.nan
    prior_day_df = close_df[close_df["Chart Date"] == prior_dates[-1]]
    if prior_day_df.empty:
        return math.nan
    return numeric_value(prior_day_df.sort_values("Datetime")["Close"].iloc[-1])


@st.cache_data(ttl=300, show_spinner=False)
def fetch_yahoo_chart_data(symbol: str, period_label: str) -> Dict[str, object]:
    option = yahoo_chart_options(period_label)
    encoded_symbol = quote(yahoo_symbol(symbol), safe="")
    response = requests.get(
        YAHOO_CHART_URL.format(symbol=encoded_symbol),
        params={
            "range": option["range"],
            "interval": option["interval"],
            "includePrePost": "false",
            "events": "history",
        },
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    chart = payload.get("chart") if isinstance(payload, dict) else {}
    error = chart.get("error") if isinstance(chart, dict) else None
    if error:
        raise RuntimeError(error.get("description") or error.get("code") or "Yahoo chart request failed.")
    results = chart.get("result") or []
    if not results:
        raise RuntimeError(f"No Yahoo chart bars returned for {symbol}.")

    result = results[0]
    timestamps = result.get("timestamp") or []
    quote_blocks = result.get("indicators", {}).get("quote") or []
    quote_block = quote_blocks[0] if quote_blocks else {}
    if not timestamps or not quote_block:
        raise RuntimeError(f"No Yahoo chart bars returned for {symbol}.")

    frame = pd.DataFrame(
        {
            "Datetime": pd.to_datetime(timestamps, unit="s", utc=True).tz_convert(None),
            "Open": quote_block.get("open") or [],
            "High": quote_block.get("high") or [],
            "Low": quote_block.get("low") or [],
            "Close": quote_block.get("close") or [],
            "Volume": quote_block.get("volume") or [],
        }
    )
    frame = frame.dropna(subset=["Datetime", "Close"]).reset_index(drop=True)
    if frame.empty:
        raise RuntimeError(f"No Yahoo chart bars returned for {symbol}.")
    previous_close = previous_close_from_prior_chart_date(frame)
    if period_label == "1D" and not frame.empty:
        latest_trading_date = pd.to_datetime(frame["Datetime"]).dt.date.max()
        frame = frame[pd.to_datetime(frame["Datetime"]).dt.date == latest_trading_date].reset_index(drop=True)

    meta_payload = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    meta = {
        "currency": meta_payload.get("currency", "USD"),
        "exchangeName": meta_payload.get("exchangeName", "Yahoo Finance"),
        "regularMarketPrice": first_valid_value(meta_payload.get("regularMarketPrice"), frame["Close"].dropna().iloc[-1]),
        "chartPreviousClose": first_valid_value(
            previous_close,
            meta_payload.get("previousClose"),
            frame["Close"].dropna().iloc[0],
        ),
        "source": "Yahoo Finance fallback",
        "from": str(frame["Datetime"].min().date()),
        "to": str(frame["Datetime"].max().date()),
        "timespan": "minute" if option["interval"].endswith("m") else "day",
        "multiplier": option["interval"],
    }
    return {"data": frame, "meta": meta}


@st.cache_data(ttl=300, show_spinner=False)
def fetch_chart_data(symbol: str, period_label: str, api_key: str) -> Dict[str, object]:
    if not api_key.strip():
        raise RuntimeError("Polygon.io API key is required. Set POLYGON_API_KEY or enter it in the sidebar.")

    option = PERIOD_OPTIONS[period_label]
    from_date, to_date = period_date_bounds(period_label)
    encoded_symbol = quote(polygon_symbol(symbol), safe="")
    response = requests.get(
        POLYGON_AGGS_URL.format(
            symbol=encoded_symbol,
            multiplier=option["multiplier"],
            timespan=option["timespan"],
            from_date=from_date,
            to_date=to_date,
        ),
        params={
            "adjusted": "true",
            "sort": "asc",
            "limit": 50000,
            "apiKey": api_key,
        },
        timeout=20,
    )
    if response.status_code == 429:
        return fetch_yahoo_chart_data(symbol, period_label)
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") not in {"OK", "DELAYED"}:
        raise RuntimeError(payload.get("error") or payload.get("message") or f"Polygon status: {payload.get('status')}")
    results = payload.get("results") or []
    if not results:
        raise RuntimeError(f"No Polygon aggregate bars returned for {symbol}.")

    frame = pd.DataFrame(
        {
            "Datetime": pd.to_datetime([row.get("t") for row in results], unit="ms", utc=True).tz_convert(None),
            "Open": [row.get("o") for row in results],
            "High": [row.get("h") for row in results],
            "Low": [row.get("l") for row in results],
            "Close": [row.get("c") for row in results],
            "Volume": [row.get("v") for row in results],
            "VWAP": [row.get("vw") for row in results],
            "Transactions": [row.get("n") for row in results],
        }
    )
    frame = frame.dropna(subset=["Datetime", "Close"]).reset_index(drop=True)
    previous_close = previous_close_from_prior_chart_date(frame)
    if period_label == "1D" and not frame.empty:
        latest_trading_date = pd.to_datetime(frame["Datetime"]).dt.date.max()
        frame = frame[pd.to_datetime(frame["Datetime"]).dt.date == latest_trading_date].reset_index(drop=True)
    if pd.isna(previous_close):
        try:
            previous_close = fetch_previous_close(symbol, api_key)
        except RuntimeError:
            previous_close = frame["Close"].dropna().iloc[0] if not frame.empty else math.nan
    meta = {
        "currency": "USD",
        "exchangeName": "Polygon.io",
        "regularMarketPrice": frame["Close"].dropna().iloc[-1] if not frame.empty else math.nan,
        "chartPreviousClose": previous_close,
        "source": "Polygon.io",
        "from": from_date,
        "to": to_date,
        "timespan": option["timespan"],
        "multiplier": option["multiplier"],
    }
    return {"data": frame, "meta": meta}


@st.cache_data(ttl=300, show_spinner=False)
def fetch_daily_history(symbol: str, api_key: str, days: int = 420) -> pd.DataFrame:
    if not api_key.strip():
        return pd.DataFrame()

    to_date = date.today().isoformat()
    from_date = (date.today() - timedelta(days=days)).isoformat()
    encoded_symbol = quote(polygon_symbol(symbol), safe="")
    try:
        response = requests.get(
            POLYGON_AGGS_URL.format(
                symbol=encoded_symbol,
                multiplier=1,
                timespan="day",
                from_date=from_date,
                to_date=to_date,
            ),
            params={
                "adjusted": "true",
                "sort": "asc",
                "limit": 50000,
                "apiKey": api_key,
            },
            timeout=20,
        )
        response.raise_for_status()
    except Exception:
        return pd.DataFrame()
    payload = response.json()
    results = payload.get("results") or []
    if not results:
        return pd.DataFrame()
    frame = pd.DataFrame(
        {
            "Datetime": pd.to_datetime([row.get("t") for row in results], unit="ms", utc=True).tz_convert(None),
            "High": [row.get("h") for row in results],
            "Low": [row.get("l") for row in results],
            "Close": [row.get("c") for row in results],
            "Volume": [row.get("v") for row in results],
        }
    )
    frame = frame.dropna(subset=["Datetime", "Close"]).reset_index(drop=True)
    for window in MOVING_AVERAGE_WINDOWS:
        frame[f"MA{window}"] = frame["Close"].rolling(window=window, min_periods=1).mean()
    if "Volume" in frame.columns:
        frame["Volume MA30"] = pd.to_numeric(frame["Volume"], errors="coerce").rolling(window=30, min_periods=1).mean()
    return frame


@st.cache_data(ttl=300, show_spinner=False)
def fetch_daily_history_range(symbol: str, api_key: str, from_date: str, to_date: str) -> pd.DataFrame:
    if not api_key.strip():
        return pd.DataFrame()

    encoded_symbol = quote(polygon_symbol(symbol), safe="")
    response = requests.get(
        POLYGON_AGGS_URL.format(
            symbol=encoded_symbol,
            multiplier=1,
            timespan="day",
            from_date=from_date,
            to_date=to_date,
        ),
        params={
            "adjusted": "true",
            "sort": "asc",
            "limit": 50000,
            "apiKey": api_key,
        },
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    results = payload.get("results") or []
    if not results:
        return pd.DataFrame()
    frame = pd.DataFrame(
        {
            "Datetime": pd.to_datetime([row.get("t") for row in results], unit="ms", utc=True).tz_convert(None),
            "Close": [row.get("c") for row in results],
        }
    )
    return frame.dropna(subset=["Datetime", "Close"]).sort_values("Datetime").reset_index(drop=True)


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_ticker_details(symbol: str, api_key: str) -> Dict[str, object]:
    if not api_key.strip():
        return {}
    encoded_symbol = quote(polygon_symbol(symbol), safe="")
    try:
        response = requests.get(
            POLYGON_TICKER_DETAILS_URL.format(symbol=encoded_symbol),
            params={"apiKey": api_key},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        return payload.get("results") or {}
    except Exception:
        return {}


@st.cache_data(ttl=30, show_spinner=False)
def fetch_snapshot_quote(symbol: str, api_key: str) -> Dict[str, object]:
    if not api_key.strip():
        return {}
    encoded_symbol = quote(polygon_symbol(symbol), safe="")
    try:
        response = requests.get(
            POLYGON_SNAPSHOT_URL.format(symbol=encoded_symbol),
            params={"apiKey": api_key},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        return payload.get("ticker") or {}
    except Exception:
        return {}


@st.cache_data(ttl=300, show_spinner=False)
def fetch_grouped_daily_quotes(market_date: str, api_key: str) -> Dict[str, Dict[str, object]]:
    if not api_key.strip() or not market_date:
        return {}
    try:
        response = requests.get(
            POLYGON_GROUPED_AGGS_URL.format(date=market_date),
            params={
                "adjusted": "true",
                "apiKey": api_key,
            },
            timeout=20,
        )
        if response.status_code == 429:
            raise RuntimeError("Polygon rate limit hit. Wait about a minute, then refresh.")
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return {}
    results = payload.get("results") or []
    grouped: Dict[str, Dict[str, object]] = {}
    for row in results:
        ticker = clean_symbol(row.get("T"))
        if ticker:
            grouped[ticker] = row
    return grouped


def stooq_symbol(symbol: str) -> str:
    cleaned = clean_symbol(symbol)
    if cleaned == "^IXIC":
        return "^ndq"
    if cleaned.startswith("^"):
        return cleaned.lower()
    if "." in cleaned:
        return cleaned.lower()
    return f"{cleaned.lower()}.us"


@st.cache_data(ttl=60, show_spinner=False)
def fetch_stooq_quotes(symbols: tuple[str, ...] | list[str]) -> Dict[str, Dict[str, object]]:
    if not symbols:
        return {}
    symbol_lookup = {stooq_symbol(symbol).upper(): clean_symbol(symbol) for symbol in symbols if clean_symbol(symbol)}
    if not symbol_lookup:
        return {}
    try:
        response = requests.get(
            STOOQ_QUOTES_URL,
            params={
                "s": " ".join(symbol_lookup.keys()).lower(),
                "f": "sd2t2ohlcv",
                "h": "",
                "e": "csv",
            },
            timeout=20,
        )
        response.raise_for_status()
    except Exception:
        return {}

    try:
        frame = pd.read_csv(StringIO(response.text))
    except Exception:
        return {}
    if frame.empty or "Symbol" not in frame.columns:
        return {}

    updates: Dict[str, Dict[str, object]] = {}
    for _, row in frame.iterrows():
        raw_symbol = str(row.get("Symbol") or "").strip().upper()
        original_symbol = symbol_lookup.get(raw_symbol)
        if not original_symbol:
            continue
        latest_price = numeric_value(row.get("Close"))
        if pd.isna(latest_price):
            continue
        quote_time = " ".join(
            item
            for item in [str(row.get("Date") or "").strip(), str(row.get("Time") or "").strip()]
            if item and item.upper() != "N/D"
        )
        update: Dict[str, object] = {
            "Current Price": latest_price,
            "Price Source": f"Stooq {quote_time}".strip(),
        }
        for source_column, target_column in [
            ("Open", "Open"),
            ("High", "High"),
            ("Low", "Low"),
            ("Volume", "Volume"),
        ]:
            value = numeric_value(row.get(source_column))
            if not pd.isna(value):
                update[target_column] = value
        updates[original_symbol] = update
    return updates


@st.cache_data(ttl=60, show_spinner=False)
def fetch_latest_aggregate_quote(symbol: str, api_key: str) -> Dict[str, object]:
    if not api_key.strip():
        return {}
    to_date = date.today().isoformat()
    from_date = (date.today() - timedelta(days=3)).isoformat()
    encoded_symbol = quote(polygon_symbol(symbol), safe="")
    try:
        response = requests.get(
            POLYGON_AGGS_URL.format(
                symbol=encoded_symbol,
                multiplier=5,
                timespan="minute",
                from_date=from_date,
                to_date=to_date,
            ),
            params={
                "adjusted": "true",
                "sort": "asc",
                "limit": 50000,
                "apiKey": api_key,
            },
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return {}
    results = payload.get("results") or []
    if not results:
        return {}

    frame = pd.DataFrame(
        {
            "Datetime": pd.to_datetime([row.get("t") for row in results], unit="ms", utc=True).tz_convert(None),
            "Open": [row.get("o") for row in results],
            "High": [row.get("h") for row in results],
            "Low": [row.get("l") for row in results],
            "Close": [row.get("c") for row in results],
            "Volume": [row.get("v") for row in results],
        }
    ).dropna(subset=["Datetime", "Close"])
    if frame.empty:
        return {}

    latest_trading_date = pd.to_datetime(frame["Datetime"]).dt.date.max()
    latest_day_df = frame[pd.to_datetime(frame["Datetime"]).dt.date == latest_trading_date].copy()
    latest_row = latest_day_df.iloc[-1]
    previous_close = fetch_previous_close(symbol, api_key)
    latest_price = numeric_value(latest_row.get("Close"))
    previous_close_number = numeric_value(previous_close)
    return {
        "Current Price": latest_price,
        "Change": latest_price - previous_close_number
        if not pd.isna(latest_price) and not pd.isna(previous_close_number)
        else math.nan,
        "Open": latest_day_df["Open"].dropna().iloc[0] if latest_day_df["Open"].notna().any() else math.nan,
        "High": latest_day_df["High"].max(),
        "Low": latest_day_df["Low"].min(),
        "Volume": latest_day_df["Volume"].sum(),
        "Previous Close": previous_close,
        "Price Source": "Polygon aggregate",
    }


@st.cache_data(ttl=60, show_spinner=False)
def fetch_latest_ticker_quote(symbol: str, api_key: str) -> Dict[str, object]:
    snapshot = fetch_snapshot_quote(symbol, api_key)
    if snapshot:
        day = snapshot.get("day") if isinstance(snapshot.get("day"), dict) else {}
        previous_day = snapshot.get("prevDay") if isinstance(snapshot.get("prevDay"), dict) else {}
        latest_price = numeric_value(snapshot_current_price(snapshot))
        previous_close = numeric_value(previous_day.get("c"))
        quote_data: Dict[str, object] = {"Price Source": "Polygon snapshot"}
        if not pd.isna(latest_price):
            quote_data["Current Price"] = latest_price
        if not pd.isna(latest_price) and not pd.isna(previous_close):
            quote_data["Change"] = latest_price - previous_close
            quote_data["Previous Close"] = previous_close
        if day.get("o") is not None:
            quote_data["Open"] = day.get("o")
        if day.get("h") is not None:
            quote_data["High"] = day.get("h")
        if day.get("l") is not None:
            quote_data["Low"] = day.get("l")
        if day.get("v") is not None:
            quote_data["Volume"] = day.get("v")
        if len(quote_data) > 1:
            return quote_data
    return fetch_latest_aggregate_quote(symbol, api_key)


def snapshot_current_price(snapshot: Dict[str, object], fallback: object = math.nan) -> object:
    last_trade = snapshot.get("lastTrade") if isinstance(snapshot, dict) else {}
    day = snapshot.get("day") if isinstance(snapshot, dict) else {}
    minute = snapshot.get("min") if isinstance(snapshot, dict) else {}
    if isinstance(last_trade, dict) and last_trade.get("p") is not None:
        return last_trade.get("p")
    if isinstance(minute, dict) and minute.get("c") is not None:
        return minute.get("c")
    if isinstance(day, dict) and day.get("c") is not None:
        return day.get("c")
    return fallback


def strip_html(value: str) -> str:
    return unescape(re.sub(r"<[^>]+>", "", value)).strip()


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_fundamentals(symbol: str) -> Dict[str, str]:
    web_symbol = polygon_symbol(symbol)
    if not web_symbol:
        return {}
    try:
        response = requests.get(
            FINVIZ_QUOTE_URL.format(symbol=quote(web_symbol, safe="")),
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=20,
        )
        response.raise_for_status()
    except Exception:
        return {}

    html = response.text

    def value_for(label: str) -> str:
        label_index = html.find(label)
        if label_index < 0:
            return "-"
        content_match = re.search(
            r'<div class="snapshot-td-content">(.*?)</div>',
            html[label_index:],
            flags=re.DOTALL,
        )
        match = content_match
        return strip_html(match.group(1)) if match else "-"

    return {
        "pe_ratio": value_for("P/E"),
        "eps_ttm": value_for("EPS (ttm)"),
        "beta": value_for("Beta"),
        "target_price": value_for("Target Price"),
        "analyst_recom": value_for("Recom"),
        "eps_next_q": value_for("EPS next Q"),
        "eps_next_y": value_for("EPS next Y"),
        "eps_next_5y": value_for("EPS next 5Y"),
        "sales_next_y": value_for("Sales next Y"),
        "sales_qoq": value_for("Sales Q/Q"),
        "eps_qoq": value_for("EPS Q/Q"),
    }


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_marketbeat_earnings_history(symbol: str) -> pd.DataFrame:
    web_symbol = polygon_symbol(symbol)
    if not web_symbol:
        return pd.DataFrame()

    exchange_candidates = ["NYSE", "NASDAQ", "NYSEARCA", "AMEX"]
    for exchange in exchange_candidates:
        try:
            response = requests.get(
                MARKETBEAT_EARNINGS_URL.format(exchange=exchange, symbol=quote(web_symbol, safe="")),
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=20,
            )
            response.raise_for_status()
        except Exception:
            continue

        html = response.text
        table_match = re.search(
            r'<table[^>]+id="earnings-history"[^>]*>(.*?)</table>',
            html,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if not table_match:
            continue

        rows = []
        for raw_row in re.findall(r"<tr[^>]*>(.*?)</tr>", table_match.group(1), flags=re.DOTALL | re.IGNORECASE):
            cells = [
                strip_html(cell).replace("\xa0", " ").strip()
                for cell in re.findall(r"<td[^>]*>(.*?)</td>", raw_row, flags=re.DOTALL | re.IGNORECASE)
            ]
            if len(cells) < 8:
                continue
            date_match = re.search(r"\d{1,2}/\d{1,2}/\d{4}", cells[0])
            if not date_match:
                continue
            if cells[3] in {"", "-"} and cells[7] in {"", "-"}:
                continue
            event_date = pd.to_datetime(date_match.group(0), errors="coerce")
            if pd.isna(event_date):
                continue
            rows.append(
                {
                    "Date": event_date.date().isoformat(),
                    "Quarter": cells[1],
                    "EPS Estimate": cells[2],
                    "Actual EPS": cells[3],
                    "EPS Surprise": cells[4],
                    "Revenue Estimate": cells[6],
                    "Actual Revenue": cells[7],
                    "Source": f"MarketBeat {exchange}",
                }
            )
        if rows:
            return pd.DataFrame(rows)

    return pd.DataFrame()


def fetch_earnings_data(symbol: str, api_key: str) -> Dict[str, object]:
    earnings_path_mtime = DEFAULT_EARNINGS_PATH.stat().st_mtime if DEFAULT_EARNINGS_PATH.exists() else 0.0
    manual_earnings = fetch_manual_earnings_data(symbol, str(DEFAULT_EARNINGS_PATH), earnings_path_mtime)
    if manual_earnings.get("event"):
        return manual_earnings

    cached_history_event = best_cached_earnings_event(symbol, DEFAULT_EARNINGS_HISTORY_PATH)
    if cached_history_event:
        cached_event_date = earnings_event_date(cached_history_event)
        cached_kind = "Next" if cached_event_date and cached_event_date > date.today() else "Last"
        return {
            "event": cached_history_event,
            "kind": cached_kind,
            "error": "",
            "source": cached_history_event.get("source", "Local earnings history"),
        }

    if not api_key.strip():
        error = "Polygon.io API key is required."
        if manual_earnings.get("error"):
            error = f"{manual_earnings['error']} {error}"
        if DEFAULT_EARNINGS_HISTORY_PATH.exists():
            error = f"No local cached earnings found for {symbol}. {error}"
        else:
            error = f"Local earnings history not found at {DEFAULT_EARNINGS_HISTORY_PATH}. {error}"
        return {"event": None, "kind": "", "error": error, "source": ""}

    ticker = polygon_symbol(symbol)
    today = date.today()

    def query_earnings(params: Dict[str, object]) -> Dict[str, object]:
        response = requests.get(
            POLYGON_EARNINGS_URL,
            params={**params, "ticker": ticker, "apiKey": api_key},
            timeout=20,
        )
        if response.status_code == 403:
            raise RuntimeError("Polygon earnings data requires Benzinga Earnings access on your Polygon.io plan.")
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") not in {None, "OK", "DELAYED"} and payload.get("error"):
            raise RuntimeError(str(payload.get("error")))
        results = payload.get("results") or []
        return results[0] if results else {}

    try:
        upcoming = query_earnings(
            {
                "date.gte": today.isoformat(),
                "date.lte": (today + timedelta(days=365)).isoformat(),
                "sort": "date",
                "order": "asc",
                "limit": 1,
            }
        )
        if upcoming:
            return {"event": upcoming, "kind": "Next", "error": ""}

        previous = query_earnings(
            {
                "date.lte": today.isoformat(),
                "sort": "date",
                "order": "desc",
                "limit": 1,
            }
        )
        if previous:
            return {"event": previous, "kind": "Last", "error": ""}
        return {"event": None, "kind": "", "error": "No earnings events returned by Polygon.", "source": "Polygon.io"}
    except Exception as exc:
        error = str(exc).strip() or exc.__class__.__name__
        if manual_earnings.get("error"):
            error = f"{manual_earnings['error']} {error}"
        return {"event": None, "kind": "", "error": error, "source": "Polygon.io"}


def auto_cache_earnings_history(symbol: str, path_text: str) -> Dict[str, object]:
    try:
        _, _, fetched_rows, fetched_symbols = scrape_portfolio_earnings_history(
            symbols=[symbol],
            output_path=Path(path_text),
            start_date=date.today() - timedelta(days=365 * 2),
            end_date=date.today(),
            force=True,
            sleep_seconds=0,
        )
        return {
            "error": "",
            "fetched_rows": fetched_rows,
            "fetched_symbols": fetched_symbols,
        }
    except Exception as exc:
        return {
            "error": str(exc).strip() or exc.__class__.__name__,
            "fetched_rows": 0,
            "fetched_symbols": [],
        }


def ensure_earnings_events_for_chart(
    symbol: str,
    selected_earnings: Dict[str, object],
    manual_earnings_event: Optional[Dict[str, object]] = None,
) -> tuple[list[Dict[str, object]], Dict[str, object]]:
    earnings_events = earnings_events_for_chart(symbol, selected_earnings, manual_earnings_event)
    if earnings_events:
        return earnings_events, {"error": "", "fetched_rows": 0, "fetched_symbols": []}

    cache_result = auto_cache_earnings_history(symbol, str(DEFAULT_EARNINGS_HISTORY_PATH))
    earnings_events = earnings_events_for_chart(symbol, selected_earnings, manual_earnings_event)
    return earnings_events, cache_result


def selected_earnings_from_events(
    current_earnings: Dict[str, object],
    earnings_events: list[Dict[str, object]],
) -> Dict[str, object]:
    if current_earnings.get("event") or not earnings_events:
        return current_earnings
    best_event = choose_best_earnings_event(
        [earnings["event"] for earnings in earnings_events if isinstance(earnings.get("event"), dict)]
    )
    return best_event if best_event.get("event") else current_earnings


def earnings_event_date(event: object) -> Optional[date]:
    if not isinstance(event, dict):
        return None
    raw_date = event.get("date") or event.get("report_date")
    if not raw_date:
        return None
    try:
        return pd.to_datetime(raw_date).date()
    except Exception:
        return None


def earnings_label(earnings: Dict[str, object]) -> str:
    event = earnings.get("event")
    event_date = earnings_event_date(event)
    if event_date is None:
        return "-"
    time_value = ""
    if isinstance(event, dict):
        time_value = str(event.get("time") or event.get("time_of_day") or "").strip()
    suffix = f" ({time_value})" if time_value else ""
    kind = str(earnings.get("kind") or "").strip()
    prefix = f"{kind}: " if kind in {"Last", "Next"} else ""
    return f"{prefix}{event_date.isoformat()}{suffix}"


def add_earnings_marker(
    fig: go.Figure,
    chart_df: pd.DataFrame,
    earnings: Dict[str, object],
    show_legend: bool = True,
) -> None:
    event_date = earnings_event_date(earnings.get("event"))
    if event_date is None:
        return

    event_timestamp = pd.Timestamp(event_date)
    chart_times = pd.to_datetime(chart_df["Datetime"])
    event_day_df = chart_df[chart_times.dt.date == event_date].copy()
    event_datetime = (
        pd.to_datetime(event_day_df["Datetime"]).min().to_pydatetime()
        if not event_day_df.empty
        else event_timestamp.to_pydatetime()
    )
    label = earnings_label(earnings)
    price_column = "High" if "High" in chart_df.columns else "Close"
    marker_source_df = event_day_df if not event_day_df.empty else chart_df
    marker_y = pd.to_numeric(marker_source_df[price_column], errors="coerce").max()

    fig.add_shape(
        type="line",
        x0=event_datetime,
        x1=event_datetime,
        y0=0,
        y1=1,
        xref="x",
        yref="paper",
        line={"color": "#f97316", "dash": "dot", "width": 1.5},
    )
    if not pd.isna(marker_y):
        fig.add_trace(
            go.Scatter(
                x=[event_datetime],
                y=[marker_y],
                mode="markers",
                name="Earnings",
                showlegend=show_legend,
                marker={"color": "#f97316", "size": 13, "symbol": "diamond"},
                hovertemplate=f"{label}<extra></extra>",
            )
        )
    fig.add_annotation(
        x=event_datetime,
        y=1,
        xref="x",
        yref="paper",
        text="Earnings",
        showarrow=False,
        yshift=12,
        font={"color": "#f97316", "size": 11},
    )


def earnings_events_for_chart(
    symbol: str,
    selected_earnings: Dict[str, object],
    manual_earnings_event: Optional[Dict[str, object]] = None,
) -> list[Dict[str, object]]:
    if manual_earnings_event:
        return [{"event": manual_earnings_event, "kind": "Manual", "error": "", "source": "Sidebar"}]

    events = [
        {"event": event, "kind": "Earnings", "error": "", "source": event.get("source", "Local earnings history")}
        for event in cached_earnings_events(symbol, DEFAULT_EARNINGS_HISTORY_PATH)
    ]
    if events:
        return events
    return [selected_earnings] if selected_earnings.get("event") else []


def visible_earnings_events(chart_df: pd.DataFrame, earnings_events: list[Dict[str, object]]) -> list[Dict[str, object]]:
    if chart_df.empty:
        return []
    min_chart_time = pd.to_datetime(chart_df["Datetime"]).min()
    max_chart_time = pd.to_datetime(chart_df["Datetime"]).max()
    visible = []
    for earnings in earnings_events:
        event_date = earnings_event_date(earnings.get("event"))
        if event_date is None:
            continue
        event_timestamp = pd.Timestamp(event_date)
        if min_chart_time <= event_timestamp <= max_chart_time:
            visible.append(earnings)
    return visible


def earnings_event_option_label(earnings: Dict[str, object]) -> str:
    event = earnings.get("event")
    event_date = earnings_event_date(event)
    if event_date is None:
        return "Unknown"
    source = ""
    if isinstance(event, dict):
        source = str(event.get("source") or "").strip()
    return f"{event_date.isoformat()}" + (f" · {source}" if source else "")


def render_earnings_event_selector(symbol: str, earnings_events: list[Dict[str, object]]) -> list[Dict[str, object]]:
    if len(earnings_events) <= 1:
        return earnings_events

    state_key = f"earnings_date_checks_{symbol}"
    event_dates = [
        event_date.isoformat()
        for earnings in earnings_events
        if (event_date := earnings_event_date(earnings.get("event"))) is not None
    ]
    if state_key not in st.session_state:
        st.session_state[state_key] = set(event_dates)

    selected_dates = set(st.session_state[state_key])
    return [
        earnings
        for earnings in earnings_events
        if (event_date := earnings_event_date(earnings.get("event"))) is not None
        and event_date.isoformat() in selected_dates
    ]


def render_earnings_event_controls(symbol: str, earnings_events: list[Dict[str, object]]) -> list[Dict[str, object]]:
    if len(earnings_events) <= 1:
        return earnings_events

    sorted_earnings_events = sorted(
        earnings_events,
        key=lambda earnings: earnings_event_date(earnings.get("event")) or date.min,
        reverse=True,
    )
    state_key = f"earnings_date_checks_{symbol}"
    event_dates = [
        event_date.isoformat()
        for earnings in sorted_earnings_events
        if (event_date := earnings_event_date(earnings.get("event"))) is not None
    ]
    if state_key not in st.session_state:
        st.session_state[state_key] = set(event_dates)

    st.markdown("**Earnings Dates**")
    all_col, none_col = st.columns(2)
    with all_col:
        select_all = st.button("All", key=f"earnings_select_all_{symbol}", use_container_width=True)
    with none_col:
        select_none = st.button("None", key=f"earnings_deselect_all_{symbol}", use_container_width=True)

    if select_all:
        st.session_state[state_key] = set(event_dates)
    if select_none:
        st.session_state[state_key] = set()

    selected_dates = set(st.session_state[state_key])
    next_selected_dates = set()
    for earnings in sorted_earnings_events:
        event_date = earnings_event_date(earnings.get("event"))
        if event_date is None:
            continue
        event_date_text = event_date.isoformat()
        checkbox_key = f"earnings_date_check_{symbol}_{event_date_text}"
        if select_all:
            st.session_state[checkbox_key] = True
        if select_none:
            st.session_state[checkbox_key] = False
        checked = st.checkbox(
            event_date_text,
            value=event_date_text in selected_dates,
            key=checkbox_key,
        )
        if checked:
            next_selected_dates.add(event_date_text)

    st.session_state[state_key] = next_selected_dates
    return [
        earnings
        for earnings in sorted_earnings_events
        if (event_date := earnings_event_date(earnings.get("event"))) is not None
        and event_date.isoformat() in next_selected_dates
    ]


def build_earnings_reaction_frame(
    symbol: str,
    earnings_events: list[Dict[str, object]],
    api_key: str,
    reaction_days: int = 10,
) -> pd.DataFrame:
    event_dates = [
        earnings_event_date(earnings.get("event"))
        for earnings in earnings_events
        if earnings_event_date(earnings.get("event")) is not None
    ]
    if not event_dates:
        return pd.DataFrame()

    from_date = (min(event_dates) - timedelta(days=10)).isoformat()
    to_date = (max(event_dates) + timedelta(days=reaction_days * 3 + 10)).isoformat()
    try:
        daily_df = fetch_daily_history_range(symbol, api_key, from_date, to_date)
    except Exception:
        return pd.DataFrame()
    if daily_df.empty:
        return pd.DataFrame()

    daily_df = daily_df.copy()
    daily_df["Date"] = pd.to_datetime(daily_df["Datetime"]).dt.date
    daily_df["Close"] = pd.to_numeric(daily_df["Close"], errors="coerce")
    daily_df = daily_df.dropna(subset=["Close"]).reset_index(drop=True)

    reaction_rows = []
    for earnings in earnings_events:
        event_date = earnings_event_date(earnings.get("event"))
        if event_date is None:
            continue
        event_candidates = daily_df[daily_df["Date"] >= event_date]
        if event_candidates.empty:
            continue
        event_index = int(event_candidates.index[0])
        baseline_index = event_index - 1
        if baseline_index < 0:
            continue
        baseline_close = float(daily_df.loc[baseline_index, "Close"])
        if not baseline_close:
            continue
        window_df = daily_df.iloc[event_index : event_index + reaction_days + 1].copy()
        for day_number, (_, row) in enumerate(window_df.iterrows()):
            reaction_rows.append(
                {
                    "Earnings Date": event_date.isoformat(),
                    "Trading Day": day_number,
                    "% Change": (float(row["Close"]) / baseline_close - 1) * 100,
                    "Close": float(row["Close"]),
                    "Price Date": row["Date"].isoformat(),
                }
            )
    return pd.DataFrame(reaction_rows)


def render_earnings_reaction_chart(
    symbol: str,
    earnings_events: list[Dict[str, object]],
    api_key: str,
    current_price: object = math.nan,
) -> None:
    st.subheader("Earnings Reaction: Day 0 To Day 10")
    chart_col, control_col = st.columns([4, 1])
    with control_col:
        selected_earnings_events = render_earnings_event_controls(symbol, earnings_events)

    if not earnings_events:
        with chart_col:
            st.info("No earnings reports available for this symbol.")
        return

    reaction_df = build_earnings_reaction_frame(symbol, selected_earnings_events, api_key)
    if reaction_df.empty:
        with chart_col:
            st.info("No earnings reaction data selected or available yet.")
        return

    current_price_number = numeric_value(current_price)
    if pd.isna(current_price_number):
        try:
            daily_df = fetch_daily_history(symbol, api_key)
            if not daily_df.empty:
                current_price_number = numeric_value(daily_df["Close"].dropna().iloc[-1])
        except Exception:
            current_price_number = math.nan

    all_reaction_df = build_earnings_reaction_frame(symbol, earnings_events, api_key)
    prior_source_df = all_reaction_df if not all_reaction_df.empty else reaction_df
    legend_labels: dict[str, str] = {}
    all_day_zero_df = prior_source_df[prior_source_df["Trading Day"] == 0].copy()
    all_day_zero_df["Parsed Earnings Date"] = pd.to_datetime(all_day_zero_df["Earnings Date"], errors="coerce")
    all_day_zero_df = all_day_zero_df.dropna(subset=["Parsed Earnings Date"]).sort_values("Parsed Earnings Date")
    day_zero_df = reaction_df[reaction_df["Trading Day"] == 0].copy()
    day_zero_df["Parsed Earnings Date"] = pd.to_datetime(day_zero_df["Earnings Date"], errors="coerce")
    day_zero_df = day_zero_df.dropna(subset=["Parsed Earnings Date"]).sort_values("Parsed Earnings Date")
    prior_earnings: dict[str, tuple[str, float]] = {}
    previous_earnings_date = ""
    previous_earnings_close = math.nan
    legend_rows = []
    for _, row in all_day_zero_df.iterrows():
        earnings_date = str(row.get("Earnings Date"))
        earnings_close = numeric_value(row.get("Close"))
        if previous_earnings_date and not pd.isna(previous_earnings_close):
            prior_earnings[earnings_date] = (previous_earnings_date, previous_earnings_close)
        previous_earnings_date = earnings_date
        previous_earnings_close = earnings_close

    for _, row in day_zero_df.iterrows():
        earnings_date = str(row.get("Earnings Date"))
        earnings_close = numeric_value(row.get("Close"))
        label_parts = [earnings_date, format_money(earnings_close)]
        today_change = math.nan
        since_prior_change = math.nan
        if pd.isna(current_price_number) or pd.isna(earnings_close) or earnings_close == 0:
            legend_labels[earnings_date] = " | ".join(label_parts)
            legend_rows.append(
                {
                    "date": earnings_date,
                    "price": earnings_close,
                    "now": today_change,
                    "prev": since_prior_change,
                    "label": legend_labels[earnings_date],
                }
            )
            continue
        today_change = (current_price_number / earnings_close - 1) * 100
        label_parts.append(f"Now {format_percent(today_change)}")
        prior_earnings_date, prior_earnings_close = prior_earnings.get(earnings_date, ("", math.nan))
        if prior_earnings_date and not pd.isna(prior_earnings_close) and prior_earnings_close:
            since_prior_change = (earnings_close / prior_earnings_close - 1) * 100
            label_parts.append(f"Prev {format_percent(since_prior_change)}")
        legend_labels[earnings_date] = " | ".join(label_parts)
        legend_rows.append(
            {
                "date": earnings_date,
                "price": earnings_close,
                "now": today_change,
                "prev": since_prior_change,
                "label": legend_labels[earnings_date],
            }
        )
    reaction_df["Earnings Legend"] = reaction_df["Earnings Date"].map(legend_labels).fillna(reaction_df["Earnings Date"])
    chart_colors = px.colors.qualitative.Plotly
    color_map = {
        row["label"]: chart_colors[index % len(chart_colors)]
        for index, row in enumerate(legend_rows)
    }
    final_change_by_label = (
        reaction_df.sort_values(["Earnings Legend", "Trading Day"])
        .groupby("Earnings Legend")["% Change"]
        .last()
        .to_dict()
    )
    legend_direction_color_map = {
        row["label"]: "#16a34a" if numeric_value(final_change_by_label.get(row["label"])) >= 0 else "#dc2626"
        for row in legend_rows
    }

    def percent_style(value: object) -> str:
        if not isinstance(value, str) or value == "-":
            return ""
        return "color: #dc2626; font-weight: 700;" if value.startswith("-") else "color: #16a34a; font-weight: 700;"

    with chart_col:
        st.caption("Day 0 is earnings-day close vs previous trading close. Days 1–10 show cumulative % change from that same baseline.")
        fig = px.line(
            reaction_df,
            x="Trading Day",
            y="% Change",
            color="Earnings Legend",
            markers=True,
            title=f"{symbol} % Change After Earnings",
            hover_data={"Earnings Date": True, "Price Date": True, "Close": ":.2f", "% Change": ":.2f"},
            color_discrete_map=color_map,
        )
        fig.add_hline(y=0, line_dash="dot", line_color="#94a3b8")
        fig.update_xaxes(dtick=1, title="Trading Days From Earnings")
        fig.update_yaxes(title="% Change From Previous Close", ticksuffix="%")
        fig.update_layout(
            height=620,
            showlegend=False,
            margin={"b": 40},
        )
        reaction_key = f"earnings_reaction_{symbol}".lower().replace(" ", "_")
        st.plotly_chart(fig, use_container_width=True, key=reaction_key)
        legend_table_rows = []
        for row in sorted(legend_rows, key=lambda item: str(item["date"]), reverse=True):
            direction_color = legend_direction_color_map.get(row["label"], "#16a34a")
            legend_table_rows.append(
                {
                    "Trend": "●",
                    "Date": str(row["date"]),
                    "Price": format_money(row["price"]),
                    "Now": format_percent(row["now"]),
                    "Prev": format_percent(row["prev"]),
                    "_trend_color": direction_color,
                }
            )
        if legend_table_rows:
            st.markdown("**Earnings Legend**")
            legend_df = pd.DataFrame(legend_table_rows)
            trend_colors = legend_df["_trend_color"].copy()
            display_legend_df = legend_df.drop(columns=["_trend_color"])

            def trend_style(row: pd.Series) -> list[str]:
                trend_color = trend_colors.loc[row.name]
                return [
                    f"color: {trend_color}; font-weight: 700;" if column == "Trend" else ""
                    for column in row.index
                ]

            st.dataframe(
                display_legend_df.style.apply(trend_style, axis=1).map(
                    percent_style,
                    subset=["Now", "Prev"],
                ),
                use_container_width=True,
                hide_index=True,
            )


def render_earnings_actuals_table(symbol: str) -> None:
    marketbeat_df = fetch_marketbeat_earnings_history(symbol)
    if not marketbeat_df.empty:
        rows = []
        for _, row in marketbeat_df.iterrows():
            projected_eps = parse_money_value(row.get("EPS Estimate"))
            actual_eps = parse_money_value(row.get("Actual EPS"))
            revenue_estimate = parse_large_money_value(row.get("Revenue Estimate"))
            actual_revenue = parse_large_money_value(row.get("Actual Revenue"))
            revenue_surprise = (
                actual_revenue - revenue_estimate
                if not pd.isna(actual_revenue) and not pd.isna(revenue_estimate)
                else math.nan
            )
            revenue_surprise_percent = (
                revenue_surprise / abs(revenue_estimate) * 100
                if revenue_estimate and not pd.isna(revenue_surprise)
                else math.nan
            )
            rows.append(
                {
                    "Date": row.get("Date"),
                    "Quarter": row.get("Quarter"),
                    "EPS Projected": format_money(projected_eps),
                    "EPS Actual": format_money(actual_eps),
                    "EPS Surprise": str(row.get("EPS Surprise") or "-").replace(" ", ""),
                    "Revenue Projected": format_large_number(revenue_estimate),
                    "Revenue Actual": format_large_number(actual_revenue),
                    "Revenue Surprise": format_large_number(revenue_surprise),
                    "Revenue Surprise %": format_percent(revenue_surprise_percent),
                }
            )

        display_df = pd.DataFrame(rows)

        def surprise_style(value: object) -> str:
            text = str(value or "")
            if text.startswith("-"):
                return "color: #dc2626; font-weight: 700;"
            if text not in {"", "-"}:
                return "color: #16a34a; font-weight: 700;"
            return ""

        st.subheader("Past Earnings: Projected vs Actual")
        st.dataframe(
            display_df.style.map(
                surprise_style,
                subset=["EPS Surprise", "Revenue Surprise", "Revenue Surprise %"],
            ),
            use_container_width=True,
            hide_index=True,
        )
        return

    history_df = read_cached_history(DEFAULT_EARNINGS_HISTORY_PATH)
    if history_df.empty or "Symbol" not in history_df.columns:
        return

    symbol_df = history_df[history_df["Symbol"].map(clean_symbol) == clean_symbol(symbol)].copy()
    if symbol_df.empty:
        return

    symbol_df["Parsed Date"] = pd.to_datetime(symbol_df["Earnings Date"], errors="coerce")
    symbol_df = symbol_df.dropna(subset=["Parsed Date"]).sort_values("Parsed Date", ascending=False)
    if symbol_df.empty:
        return

    rows = []
    for _, row in symbol_df.iterrows():
        actual_eps = parse_money_value(row.get("EPS"))
        projected_eps = parse_money_value(row.get("EPS Forecast"))
        surprise = actual_eps - projected_eps if not pd.isna(actual_eps) and not pd.isna(projected_eps) else math.nan
        surprise_percent = (surprise / abs(projected_eps) * 100) if projected_eps and not pd.isna(surprise) else math.nan
        rows.append(
            {
                "Date": row["Parsed Date"].date().isoformat(),
                "Quarter": str(row.get("Fiscal Quarter Ending") or ""),
                "EPS Projected": format_money(projected_eps),
                "EPS Actual": format_money(actual_eps),
                "EPS Surprise": format_money(surprise),
                "EPS Surprise %": format_percent(surprise_percent),
                "Revenue Projected": "-",
                "Revenue Actual": "-",
                "Revenue Surprise": "-",
                "Revenue Surprise %": "-",
            }
        )

    display_df = pd.DataFrame(rows)
    if display_df.empty:
        return

    def surprise_style(value: object) -> str:
        text = str(value or "")
        if text.startswith("-"):
            return "color: #dc2626; font-weight: 700;"
        if text not in {"", "-"}:
            return "color: #16a34a; font-weight: 700;"
        return ""

    st.subheader("Past Earnings: Projected vs Actual")
    st.dataframe(
        display_df.style.map(surprise_style, subset=["EPS Surprise", "EPS Surprise %"]),
        use_container_width=True,
        hide_index=True,
    )


def render_intraday_chart(symbol: str, api_key: str) -> None:
    try:
        result = fetch_chart_data(symbol, "1D", api_key)
    except Exception as exc:
        st.info(f"Intraday chart unavailable: {exc}")
        return

    intraday_df = result["data"]
    if intraday_df.empty:
        st.info("No intraday bars available.")
        return

    intraday_date = pd.to_datetime(intraday_df["Datetime"]).dt.date.max()
    st.subheader(f"Intraday Chart: {intraday_date.isoformat()}")
    intraday_col, volume_col = st.columns([3, 1])
    with intraday_col:
        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=intraday_df["Datetime"],
                y=intraday_df["Close"],
                mode="lines",
                name="Price",
                line={"color": "#2563eb", "width": 2.4},
                hovertemplate="Time=%{x}<br>Price=%{y:.2f}<extra></extra>",
            )
        )
        previous_close = result["meta"].get("chartPreviousClose")
        if not pd.isna(numeric_value(previous_close)):
            fig.add_hline(
                y=previous_close,
                line_dash="dot",
                line_color="#94a3b8",
                annotation_text=f"Prev Close {format_money(previous_close)}",
            )
        apply_market_time_axis(fig, intraday_df, skip_closed_hours=True)
        fig.update_layout(height=360, title=f"{symbol} Intraday {intraday_date.isoformat()}")
        fig.update_yaxes(title="Price")
        st.plotly_chart(fig, use_container_width=True, key=f"intraday_price_{symbol}".lower())

    with volume_col:
        latest_row = intraday_df.dropna(subset=["Close"]).iloc[-1]
        open_price = numeric_value(intraday_df["Open"].dropna().iloc[0]) if "Open" in intraday_df.columns else math.nan
        latest_price = numeric_value(latest_row.get("Close"))
        day_change = latest_price - open_price if not pd.isna(latest_price) and not pd.isna(open_price) else math.nan
        day_change_percent = (day_change / open_price * 100) if open_price and not pd.isna(day_change) else math.nan
        st.metric("Latest", format_money(latest_price), format_percent(day_change_percent))
        st.metric("Open", format_money(open_price))
        st.metric("High", format_money(intraday_df["High"].max() if "High" in intraday_df.columns else math.nan))
        st.metric("Low", format_money(intraday_df["Low"].min() if "Low" in intraday_df.columns else math.nan))
        st.metric("Volume", format_large_number(intraday_df["Volume"].sum() if "Volume" in intraday_df.columns else math.nan))


def visible_daily_history(symbol: str, chart_df: pd.DataFrame, api_key: str) -> pd.DataFrame:
    try:
        ma_df = fetch_daily_history(symbol, api_key)
    except Exception:
        return pd.DataFrame()
    if ma_df.empty or chart_df.empty or "Datetime" not in chart_df.columns:
        return pd.DataFrame()

    min_chart_time = pd.to_datetime(chart_df["Datetime"]).min()
    max_chart_time = pd.to_datetime(chart_df["Datetime"]).max()
    chart_times = pd.to_datetime(chart_df["Datetime"])
    is_intraday_chart = (chart_times.dt.normalize() != chart_times).any()
    if is_intraday_chart:
        daily_df = ma_df.sort_values("Datetime").copy()
        daily_df["Chart Date"] = pd.to_datetime(daily_df["Datetime"]).dt.normalize()
        intraday_df = chart_df[["Datetime"]].dropna().sort_values("Datetime").copy()
        intraday_df["Chart Date"] = pd.to_datetime(intraday_df["Datetime"]).dt.normalize()
        projected_ma_df = pd.merge_asof(
            intraday_df,
            daily_df.drop(columns=["Datetime"]).sort_values("Chart Date"),
            on="Chart Date",
            direction="backward",
        ).drop(columns=["Chart Date"])
        return projected_ma_df.dropna(subset=["Datetime"]).reset_index(drop=True)

    visible_ma_df = ma_df[
        (pd.to_datetime(ma_df["Datetime"]) >= min_chart_time)
        & (pd.to_datetime(ma_df["Datetime"]) <= max_chart_time)
    ].copy()
    if visible_ma_df.empty:
        visible_ma_df = ma_df.tail(min(len(ma_df), 60)).copy()
    return visible_ma_df


def add_moving_average_lines(
    fig: go.Figure,
    visible_ma_df: pd.DataFrame,
    selected_windows: list[int],
) -> None:
    if visible_ma_df.empty or not selected_windows:
        return
    colors = {
        10: "#22c55e",
        20: "#3b82f6",
        50: "#f97316",
        200: "#a855f7",
    }
    for window in selected_windows:
        column = f"MA{window}"
        if column not in visible_ma_df.columns:
            continue
        fig.add_trace(
            go.Scatter(
                x=visible_ma_df["Datetime"],
                y=visible_ma_df[column],
                mode="lines",
                name=f"{window}D MA",
                line={"width": 1.8, "color": colors.get(window)},
                connectgaps=True,
            )
        )


def moving_average_crossover_rows(ma_df: pd.DataFrame, pairs: list[tuple[int, int]]) -> pd.DataFrame:
    if ma_df.empty:
        return pd.DataFrame()
    rows = []
    enriched_df = ma_df.copy()
    enriched_df["Previous Close"] = pd.to_numeric(enriched_df.get("Close"), errors="coerce").shift(1)
    enriched_df["Previous Volume"] = pd.to_numeric(enriched_df.get("Volume"), errors="coerce").shift(1)
    for fast_window, slow_window in pairs:
        fast_column = f"MA{fast_window}"
        slow_column = f"MA{slow_window}"
        if fast_column not in enriched_df.columns or slow_column not in enriched_df.columns:
            continue
        crossover_df = enriched_df[
            ["Datetime", "Close", "Previous Close", "Volume", "Previous Volume", "Volume MA30", fast_column, slow_column]
        ].dropna(subset=["Datetime", "Close", fast_column, slow_column]).copy()
        if crossover_df.empty:
            continue
        difference = crossover_df[fast_column] - crossover_df[slow_column]
        previous_difference = difference.shift(1)
        signals = [
            ((difference > 0) & (previous_difference <= 0), "Bullish"),
            ((difference < 0) & (previous_difference >= 0), "Bearish"),
        ]
        for mask, direction in signals:
            for _, row in crossover_df[mask].iterrows():
                volume = numeric_value(row.get("Volume"))
                close = numeric_value(row.get("Close"))
                previous_close = numeric_value(row.get("Previous Close"))
                previous_volume = numeric_value(row.get("Previous Volume"))
                volume_ma30 = numeric_value(row.get("Volume MA30"))
                rows.append(
                    {
                        "Date": pd.to_datetime(row["Datetime"]).date().isoformat(),
                        "Pattern": "Cross",
                        "Cross": f"{fast_window}D / {slow_window}D",
                        "Signal": direction,
                        "Price": close,
                        "Price Change vs Prior Day": (
                            (close / previous_close - 1) * 100
                            if previous_close and not pd.isna(previous_close) and not pd.isna(close)
                            else math.nan
                        ),
                        "Volume": volume,
                        "Volume Change vs Prior Day": (
                            (volume / previous_volume - 1) * 100
                            if previous_volume and not pd.isna(previous_volume) and not pd.isna(volume)
                            else math.nan
                        ),
                        "Volume vs 30D Avg": (
                            (volume / volume_ma30 - 1) * 100
                            if volume_ma30 and not pd.isna(volume_ma30) and not pd.isna(volume)
                            else math.nan
                        ),
                    }
                )
    return pd.DataFrame(rows).sort_values("Date", ascending=False) if rows else pd.DataFrame()


def add_moving_average_crossovers(fig: go.Figure, ma_df: pd.DataFrame, pairs: list[tuple[int, int]]) -> pd.DataFrame:
    if ma_df.empty:
        return pd.DataFrame()
    crossover_rows = moving_average_crossover_rows(ma_df, pairs)
    shown_legend = False
    for _, row in crossover_rows.iterrows():
        is_bullish = row["Signal"] == "Bullish"
        is_long_term_cross = row["Cross"] == "50D / 200D"
        marker_color = "#ca8a04" if is_long_term_cross and is_bullish else "#7f1d1d" if is_long_term_cross else "#16a34a" if is_bullish else "#dc2626"
        marker_label = "Golden Cross" if is_long_term_cross and is_bullish else "Death Cross" if is_long_term_cross else "MA Cross"
        fig.add_trace(
            go.Scatter(
                x=[pd.to_datetime(row["Date"])],
                y=[row["Price"]],
                mode="markers",
                name=marker_label,
                showlegend=not shown_legend,
                marker={
                    "color": marker_color,
                    "size": 22 if is_long_term_cross else 14,
                    "symbol": "star" if is_long_term_cross else "triangle-up" if is_bullish else "triangle-down",
                    "line": {"color": "white", "width": 2 if is_long_term_cross else 1},
                },
                text=[f"{marker_label}: {row['Cross']} {row['Signal'].lower()}"],
                hovertemplate="%{text}<br>Date=%{x}<br>Price=%{y:.2f}<extra></extra>",
            )
        )
        shown_legend = True
    return crossover_rows


def render_patterns_table(patterns_df: pd.DataFrame) -> None:
    if patterns_df.empty:
        return
    st.subheader("Patterns")
    display_df = patterns_df.copy()
    display_df["Price"] = display_df["Price"].map(lambda value: f"${value:,.2f}" if not pd.isna(value) else "-")
    if "Price Change vs Prior Day" in display_df.columns:
        display_df["Price Change vs Prior Day"] = display_df["Price Change vs Prior Day"].map(format_percent)
    display_df["Volume"] = display_df["Volume"].map(lambda value: format_large_number(value))
    display_df["Volume Change vs Prior Day"] = display_df["Volume Change vs Prior Day"].map(format_percent)
    display_df["Volume vs 30D Avg"] = display_df["Volume vs 30D Avg"].map(format_percent)

    def signal_style(value: object) -> str:
        if value == "Bullish":
            return "color: #16a34a; font-weight: 700;"
        if value == "Bearish":
            return "color: #dc2626; font-weight: 700;"
        return ""

    def long_term_cross_style(row: pd.Series) -> list[str]:
        style = "background-color: #fef3c7; font-weight: 700;" if row.get("Cross") == "50D / 200D" else ""
        return [style for _ in row.index]

    st.dataframe(
        display_df.style.apply(long_term_cross_style, axis=1).map(signal_style, subset=["Signal"]),
        use_container_width=True,
        hide_index=True,
    )


def render_long_term_cross_chart(symbol: str, ma_df: pd.DataFrame, crossover_df: pd.DataFrame) -> None:
    if ma_df.empty or not {"MA50", "MA200", "Datetime"}.issubset(ma_df.columns):
        return
    spread_df = ma_df[["Datetime", "MA50", "MA200"]].dropna().copy()
    if spread_df.empty:
        return
    spread_df["50D vs 200D %"] = (spread_df["MA50"] / spread_df["MA200"] - 1) * 100
    estimate_df = spread_df.dropna(subset=["50D vs 200D %"]).tail(20).reset_index(drop=True)
    current_spread = numeric_value(spread_df["50D vs 200D %"].dropna().iloc[-1])
    projected_date = None
    projected_days = math.nan
    daily_slope = math.nan
    if len(estimate_df) >= 5:
        x_values = pd.Series(range(len(estimate_df)), dtype="float")
        y_values = pd.to_numeric(estimate_df["50D vs 200D %"], errors="coerce")
        x_mean = x_values.mean()
        y_mean = y_values.mean()
        denominator = ((x_values - x_mean) ** 2).sum()
        if denominator:
            daily_slope = ((x_values - x_mean) * (y_values - y_mean)).sum() / denominator
            if current_spread < 0 and daily_slope > 0:
                projected_days = math.ceil(abs(current_spread) / daily_slope)
                latest_spread_date = pd.to_datetime(spread_df["Datetime"].iloc[-1]).date()
                projected_date = add_trading_days(latest_spread_date, int(projected_days))

    fig = px.line(
        spread_df,
        x="Datetime",
        y="50D vs 200D %",
        title=f"{symbol} 50D / 200D Spread",
    )
    fig.update_traces(line={"color": "#ca8a04", "width": 2.6})
    fig.add_hline(y=0, line_dash="dot", line_color="#64748b")
    long_term_crosses = crossover_df[crossover_df["Cross"] == "50D / 200D"].copy() if not crossover_df.empty else pd.DataFrame()
    for _, row in long_term_crosses.iterrows():
        cross_date = pd.to_datetime(row["Date"])
        nearest = spread_df.iloc[(pd.to_datetime(spread_df["Datetime"]) - cross_date).abs().argsort()[:1]]
        if nearest.empty:
            continue
        is_bullish = row["Signal"] == "Bullish"
        price_text = format_money(row.get("Price"))
        price_change_text = format_percent(row.get("Price Change vs Prior Day"))
        volume_text = format_large_number(row.get("Volume"))
        volume_vs_30d_text = format_percent(row.get("Volume vs 30D Avg"))
        fig.add_trace(
            go.Scatter(
                x=nearest["Datetime"],
                y=nearest["50D vs 200D %"],
                mode="markers",
                name="Golden Cross" if is_bullish else "Death Cross",
                marker={
                    "color": "#16a34a" if is_bullish else "#dc2626",
                    "size": 16,
                    "symbol": "star",
                    "line": {"color": "white", "width": 1.5},
                },
                hovertemplate=(
                    f"{row['Signal']} 50D / 200D"
                    f"<br>Date=%{{x}}"
                    f"<br>Spread=%{{y:.2f}}%"
                    f"<br>Price={price_text}"
                    f"<br>Price Change={price_change_text}"
                    f"<br>Volume={volume_text}"
                    f"<br>Volume vs 30D={volume_vs_30d_text}"
                    "<extra></extra>"
                ),
            )
        )
    if projected_date is not None:
        fig.add_vline(x=projected_date, line_dash="dash", line_color="#16a34a")
        fig.add_annotation(
            x=projected_date,
            y=0,
            text=f"Est. positive {projected_date.isoformat()}",
            showarrow=True,
            arrowhead=2,
            ax=30,
            ay=-40,
        )
    apply_market_time_axis(fig, spread_df)
    fig.update_yaxes(title="50D Above/Below 200D", ticksuffix="%")
    fig.update_layout(height=320, legend={"orientation": "h", "y": -0.2})
    st.plotly_chart(fig, use_container_width=True, key=f"long_term_cross_{symbol}".lower())
    if not long_term_crosses.empty:
        display_crosses = long_term_crosses[
            [
                "Date",
                "Signal",
                "Price",
                "Price Change vs Prior Day",
                "Volume",
                "Volume vs 30D Avg",
            ]
        ].copy()
        display_crosses["Price"] = display_crosses["Price"].map(format_money)
        display_crosses["Price Change vs Prior Day"] = display_crosses["Price Change vs Prior Day"].map(format_percent)
        display_crosses["Volume"] = display_crosses["Volume"].map(format_large_number)
        display_crosses["Volume vs 30D Avg"] = display_crosses["Volume vs 30D Avg"].map(format_percent)
        st.dataframe(display_crosses, use_container_width=True, hide_index=True)
    if current_spread >= 0:
        st.success(f"50D/200D is already positive: {format_percent(current_spread)}.")
    elif projected_date is not None:
        st.info(
            f"At the recent 20-day trend, 50D/200D turns positive around "
            f"{projected_date.isoformat()} ({projected_days} trading days)."
        )
    elif not pd.isna(daily_slope) and daily_slope <= 0:
        st.warning("50D/200D is still negative and not improving on the recent 20-day trend.")


@st.cache_data(ttl=300, show_spinner=False)
def scan_symbol_patterns(symbol: str, api_key: str) -> pd.DataFrame:
    try:
        ma_df = fetch_daily_history(symbol, api_key)
    except Exception:
        return pd.DataFrame()
    if ma_df.empty:
        return pd.DataFrame()
    patterns_df = moving_average_crossover_rows(ma_df, [(10, 20), (20, 50), (50, 200)])
    if patterns_df.empty:
        return pd.DataFrame()
    patterns_df.insert(0, "Symbol", symbol)
    return patterns_df


def render_all_patterns_page(symbols: list[str], api_key: str) -> None:
    st.subheader("Patterns Across Tickers")
    if not api_key.strip():
        st.warning("Polygon.io API key is required to scan patterns.")
        return

    days_filter = st.slider("Last N days", min_value=7, max_value=365, value=120, step=7)
    selected_crosses = st.multiselect(
        "Cross",
        ["10D / 20D", "20D / 50D", "50D / 200D"],
        default=["10D / 20D", "20D / 50D", "50D / 200D"],
    )
    selected_signals = st.multiselect("Signal", ["Bullish", "Bearish"], default=["Bullish", "Bearish"])

    unique_symbols = sorted({clean_symbol(symbol) for symbol in symbols if clean_symbol(symbol)})
    if not unique_symbols:
        st.info("No tickers available.")
        return

    default_graph_symbols = unique_symbols[: min(len(unique_symbols), 3)]
    graph_symbols = st.multiselect(
        "50D / 200D Graphs",
        options=unique_symbols,
        default=default_graph_symbols,
        key="patterns_long_term_graph_symbols",
        help="Pick one or more tickers to show the 50D/200D spread graph.",
    )
    for graph_symbol in graph_symbols:
        graph_ma_df = fetch_daily_history(graph_symbol, api_key)
        graph_crossover_df = moving_average_crossover_rows(graph_ma_df, [(10, 20), (20, 50), (50, 200)])
        render_long_term_cross_chart(graph_symbol, graph_ma_df, graph_crossover_df)

    progress = st.progress(0, text="Scanning tickers...")
    pattern_frames = []
    for index, symbol in enumerate(unique_symbols, start=1):
        symbol_patterns = scan_symbol_patterns(symbol, api_key)
        if not symbol_patterns.empty:
            pattern_frames.append(symbol_patterns)
        progress.progress(index / max(len(unique_symbols), 1), text=f"Scanning {symbol} ({index}/{len(unique_symbols)})")
    progress.empty()

    if not pattern_frames:
        st.info("No crossover patterns found for current tickers.")
        return

    patterns_df = pd.concat(pattern_frames, ignore_index=True)
    patterns_df["Parsed Date"] = pd.to_datetime(patterns_df["Date"], errors="coerce")
    cutoff = pd.Timestamp(date.today() - timedelta(days=days_filter))
    patterns_df = patterns_df[patterns_df["Parsed Date"] >= cutoff].copy()
    if selected_crosses:
        patterns_df = patterns_df[patterns_df["Cross"].isin(selected_crosses)].copy()
    if selected_signals:
        patterns_df = patterns_df[patterns_df["Signal"].isin(selected_signals)].copy()
    if patterns_df.empty:
        st.info("No patterns match the selected filters.")
        return

    patterns_df = patterns_df.sort_values(["Parsed Date", "Symbol"], ascending=[False, True])
    display_df = patterns_df.drop(columns=["Parsed Date"]).copy()
    display_df["Price"] = display_df["Price"].map(format_money)
    display_df["Price Change vs Prior Day"] = display_df["Price Change vs Prior Day"].map(format_percent)
    display_df["Volume"] = display_df["Volume"].map(format_large_number)
    display_df["Volume Change vs Prior Day"] = display_df["Volume Change vs Prior Day"].map(format_percent)
    display_df["Volume vs 30D Avg"] = display_df["Volume vs 30D Avg"].map(format_percent)

    metric_1, metric_2, metric_3, metric_4 = st.columns(4)
    metric_1.metric("Tickers", len(unique_symbols))
    metric_2.metric("Patterns", len(display_df))
    metric_3.metric("Bullish", int((display_df["Signal"] == "Bullish").sum()))
    metric_4.metric("50D / 200D", int((display_df["Cross"] == "50D / 200D").sum()))

    def signal_style(value: object) -> str:
        if value == "Bullish":
            return "color: #16a34a; font-weight: 700;"
        if value == "Bearish":
            return "color: #dc2626; font-weight: 700;"
        return ""

    def long_term_cross_style(row: pd.Series) -> list[str]:
        style = "background-color: #fef3c7; font-weight: 700;" if row.get("Cross") == "50D / 200D" else ""
        return [style for _ in row.index]

    st.dataframe(
        display_df.style.apply(long_term_cross_style, axis=1).map(signal_style, subset=["Signal"]),
        use_container_width=True,
        hide_index=True,
    )


@st.cache_data(ttl=60, show_spinner=False)
def day_trade_signal_row(symbol: str, api_key: str) -> Dict[str, object]:
    try:
        result = fetch_chart_data(symbol, "1D", api_key)
    except Exception as exc:
        return {"Symbol": symbol, "Status": f"Error: {exc}"}

    intraday_df = result["data"].copy()
    if intraday_df.empty:
        return {"Symbol": symbol, "Status": "No intraday data"}

    intraday_df["Close"] = pd.to_numeric(intraday_df["Close"], errors="coerce")
    intraday_df["High"] = pd.to_numeric(intraday_df.get("High"), errors="coerce")
    intraday_df["Low"] = pd.to_numeric(intraday_df.get("Low"), errors="coerce")
    intraday_df["Volume"] = pd.to_numeric(intraday_df.get("Volume"), errors="coerce")
    intraday_df = intraday_df.dropna(subset=["Close"])
    if intraday_df.empty:
        return {"Symbol": symbol, "Status": "No price bars"}

    latest = intraday_df.iloc[-1]
    latest_price = numeric_value(latest.get("Close"))
    open_price = numeric_value(intraday_df["Open"].dropna().iloc[0]) if "Open" in intraday_df.columns and not intraday_df["Open"].dropna().empty else math.nan
    day_high = numeric_value(intraday_df["High"].max())
    day_low = numeric_value(intraday_df["Low"].min())
    day_range = day_high - day_low if not pd.isna(day_high) and not pd.isna(day_low) else math.nan
    range_percent = (day_range / day_low * 100) if day_low and not pd.isna(day_range) else math.nan
    position_in_range = ((latest_price - day_low) / day_range * 100) if day_range and not pd.isna(latest_price) else math.nan
    from_open_percent = ((latest_price / open_price) - 1) * 100 if open_price and not pd.isna(latest_price) else math.nan

    previous_close = numeric_value(result["meta"].get("chartPreviousClose"))
    from_previous_close_percent = (
        ((latest_price / previous_close) - 1) * 100
        if previous_close and not pd.isna(latest_price)
        else math.nan
    )

    close_series = intraday_df["Close"].dropna()
    trend_5 = ((close_series.iloc[-1] / close_series.iloc[-6]) - 1) * 100 if len(close_series) >= 6 and close_series.iloc[-6] else math.nan
    trend_20 = ((close_series.iloc[-1] / close_series.iloc[-21]) - 1) * 100 if len(close_series) >= 21 and close_series.iloc[-21] else math.nan
    vwap = numeric_value(latest.get("VWAP")) if "VWAP" in intraday_df.columns else math.nan
    above_vwap = not pd.isna(vwap) and latest_price >= vwap

    opening_rows = intraday_df.head(min(len(intraday_df), 6))
    opening_high = numeric_value(opening_rows["High"].max()) if not opening_rows.empty else math.nan
    opening_low = numeric_value(opening_rows["Low"].min()) if not opening_rows.empty else math.nan
    near_high = not pd.isna(position_in_range) and position_in_range >= 80
    near_low = not pd.isna(position_in_range) and position_in_range <= 20
    opening_breakout = not pd.isna(opening_high) and latest_price > opening_high
    opening_breakdown = not pd.isna(opening_low) and latest_price < opening_low

    daily_df = fetch_daily_history(symbol, api_key)
    avg_daily_volume = pd.to_numeric(daily_df["Volume"], errors="coerce").tail(30).mean() if not daily_df.empty and "Volume" in daily_df.columns else math.nan
    intraday_volume = numeric_value(intraday_df["Volume"].sum())
    volume_vs_avg = (intraday_volume / avg_daily_volume * 100) if avg_daily_volume and not pd.isna(intraday_volume) else math.nan

    patterns = []
    if trend_5 > 0 and trend_20 > 0:
        patterns.append("Positive trend")
    if above_vwap:
        patterns.append("Above VWAP")
    if near_high:
        patterns.append("Near high")
    if near_low:
        patterns.append("Near low")
    if opening_breakout:
        patterns.append("Opening breakout")
    if opening_breakdown:
        patterns.append("Opening breakdown")
    if not pd.isna(volume_vs_avg) and volume_vs_avg >= 60:
        patterns.append("High volume pace")

    score = 0
    score += 2 if trend_5 > 0 else -1 if trend_5 < 0 else 0
    score += 2 if trend_20 > 0 else -1 if trend_20 < 0 else 0
    score += 1 if above_vwap else -1
    score += 1 if near_high else 0
    score += 1 if opening_breakout else 0
    score -= 1 if near_low or opening_breakdown else 0

    signal = "Watch"
    if score >= 5:
        signal = "Strong Long"
    elif score >= 3:
        signal = "Long Bias"
    elif score <= -3:
        signal = "Weak / Avoid"

    return {
        "Symbol": symbol,
        "Signal": signal,
        "Score": score,
        "Price": latest_price,
        "Day Low": day_low,
        "Day High": day_high,
        "Range %": range_percent,
        "Position In Range": position_in_range,
        "% From Open": from_open_percent,
        "% From Prev Close": from_previous_close_percent,
        "5-Bar Trend": trend_5,
        "20-Bar Trend": trend_20,
        "Volume": intraday_volume,
        "Volume vs 30D Avg": volume_vs_avg,
        "Patterns": ", ".join(patterns) if patterns else "-",
        "Status": "OK",
    }


@st.cache_data(ttl=300, show_spinner=False)
def fetch_intraday_history(symbol: str, api_key: str, trading_days: int = 10) -> pd.DataFrame:
    if not api_key.strip():
        return pd.DataFrame()

    end_date = previous_trading_day(date.today())
    start_date = add_trading_days(end_date, -(trading_days + 3))
    encoded_symbol = quote(polygon_symbol(symbol), safe="")
    try:
        response = requests.get(
            POLYGON_AGGS_URL.format(
                symbol=encoded_symbol,
                multiplier=5,
                timespan="minute",
                from_date=start_date.isoformat(),
                to_date=end_date.isoformat(),
            ),
            params={
                "adjusted": "true",
                "sort": "asc",
                "limit": 50000,
                "apiKey": api_key,
            },
            timeout=20,
        )
        response.raise_for_status()
    except Exception:
        return pd.DataFrame()

    payload = response.json()
    results = payload.get("results") or []
    if not results:
        return pd.DataFrame()

    frame = pd.DataFrame(
        {
            "Datetime": pd.to_datetime([row.get("t") for row in results], unit="ms", utc=True).tz_convert(None),
            "Open": [row.get("o") for row in results],
            "High": [row.get("h") for row in results],
            "Low": [row.get("l") for row in results],
            "Close": [row.get("c") for row in results],
            "Volume": [row.get("v") for row in results],
        }
    )
    frame = frame.dropna(subset=["Datetime", "Close"]).reset_index(drop=True)
    if frame.empty:
        return frame
    eastern_time = pd.to_datetime(frame["Datetime"]).dt.tz_localize("UTC").dt.tz_convert("America/New_York")
    frame["Market Date"] = eastern_time.dt.date
    frame["Market Time"] = eastern_time.dt.strftime("%H:%M")
    trading_dates = sorted(frame["Market Date"].dropna().unique())[-trading_days:]
    return frame[frame["Market Date"].isin(trading_dates)].reset_index(drop=True)


def build_day_trade_range_tables(symbol: str, api_key: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    intraday_df = fetch_intraday_history(symbol, api_key, trading_days=10)
    if intraday_df.empty:
        return pd.DataFrame(), pd.DataFrame()

    range_rows = []
    first_two_hour_rows = []
    for market_date, day_df in intraday_df.groupby("Market Date", sort=True):
        day_df = day_df.sort_values("Datetime").reset_index(drop=True).copy()
        day_df["Market Minutes"] = pd.to_datetime(day_df["Market Time"], format="%H:%M", errors="coerce").dt.hour * 60 + pd.to_datetime(
            day_df["Market Time"], format="%H:%M", errors="coerce"
        ).dt.minute
        day_df = day_df[(day_df["Market Minutes"] >= 570) & (day_df["Market Minutes"] <= 960)].reset_index(drop=True)
        if day_df.empty:
            continue
        day_low_index = day_df["Low"].astype(float).idxmin()
        day_high_index = day_df["High"].astype(float).idxmax()
        day_low = numeric_value(day_df.loc[day_low_index, "Low"])
        day_high = numeric_value(day_df.loc[day_high_index, "High"])
        day_range = day_high - day_low if not pd.isna(day_high) and not pd.isna(day_low) else math.nan
        range_rows.append(
            {
                "Date": market_date.isoformat(),
                "Low": day_low,
                "Low Time": day_df.loc[day_low_index, "Market Time"],
                "High": day_high,
                "High Time": day_df.loc[day_high_index, "Market Time"],
                "Range": day_range,
                "Range %": (day_range / day_low * 100) if day_low and not pd.isna(day_range) else math.nan,
                "Volume": numeric_value(day_df["Volume"].sum()),
            }
        )

        open_minute = int(day_df["Market Minutes"].dropna().iloc[0]) if not day_df["Market Minutes"].dropna().empty else 570
        open_window_df = day_df[
            (day_df["Market Minutes"] >= open_minute)
            & (day_df["Market Minutes"] <= open_minute + 120)
        ].copy()
        if open_window_df.empty:
            continue
        open_price = numeric_value(open_window_df.iloc[0].get("Open"))
        first_two_high_index = open_window_df["High"].astype(float).idxmax()
        first_two_low_index = open_window_df["Low"].astype(float).idxmin()
        first_two_high = numeric_value(day_df.loc[first_two_high_index, "High"])
        first_two_low = numeric_value(day_df.loc[first_two_low_index, "Low"])
        close_2h = numeric_value(open_window_df.iloc[-1].get("Close"))
        high_from_open = (first_two_high / open_price - 1) * 100 if open_price and not pd.isna(first_two_high) else math.nan
        dip_from_open = (first_two_low / open_price - 1) * 100 if open_price and not pd.isna(first_two_low) else math.nan
        recovery_from_low = (close_2h / first_two_low - 1) * 100 if first_two_low and not pd.isna(close_2h) else math.nan
        close_2h_from_open = (close_2h / open_price - 1) * 100 if open_price and not pd.isna(close_2h) else math.nan
        pattern = "Chop"
        if high_from_open > 0.6 and dip_from_open < -0.6 and recovery_from_low > 0.6:
            pattern = "Open high → dip → pull-up"
        elif high_from_open > 0.6 and close_2h_from_open > 0.4:
            pattern = "Opening strength"
        elif dip_from_open < -0.6 and recovery_from_low < 0.3:
            pattern = "Weak open / no recovery"
        elif dip_from_open < -0.6 and recovery_from_low >= 0.6:
            pattern = "Dip recovery"

        first_two_hour_rows.append(
            {
                "Date": market_date.isoformat(),
                "Open Time": str(open_window_df.iloc[0].get("Market Time") or ""),
                "Open": open_price,
                "2H High": first_two_high,
                "High Time": day_df.loc[first_two_high_index, "Market Time"],
                "2H Low": first_two_low,
                "Low Time": day_df.loc[first_two_low_index, "Market Time"],
                "2H Close": close_2h,
                "High From Open": high_from_open,
                "Dip From Open": dip_from_open,
                "Recovery From Low": recovery_from_low,
                "2H Close From Open": close_2h_from_open,
                "Pattern": pattern,
            }
        )

    return pd.DataFrame(range_rows).sort_values("Date", ascending=False), pd.DataFrame(first_two_hour_rows).sort_values("Date", ascending=False)


def render_first_two_hour_chart(symbol: str, api_key: str) -> None:
    intraday_df = fetch_intraday_history(symbol, api_key, trading_days=10)
    if intraday_df.empty:
        return

    chart_rows = []
    for market_date, day_df in intraday_df.groupby("Market Date", sort=True):
        day_df = day_df.sort_values("Datetime").reset_index(drop=True).copy()
        parsed_time = pd.to_datetime(day_df["Market Time"], format="%H:%M", errors="coerce")
        day_df["Market Minutes"] = parsed_time.dt.hour * 60 + parsed_time.dt.minute
        day_df = day_df[(day_df["Market Minutes"] >= 570) & (day_df["Market Minutes"] <= 690)].reset_index(drop=True)
        if day_df.empty:
            continue
        open_minute = int(day_df["Market Minutes"].iloc[0])
        open_price = numeric_value(day_df.iloc[0].get("Open"))
        if not open_price or pd.isna(open_price):
            continue
        for _, row in day_df.iterrows():
            minutes_from_open = int(row["Market Minutes"] - open_minute)
            close_price = numeric_value(row.get("Close"))
            chart_rows.append(
                {
                    "Date": market_date.isoformat(),
                    "Minutes From Open": minutes_from_open,
                    "% From Open": (close_price / open_price - 1) * 100 if not pd.isna(close_price) else math.nan,
                    "Price": close_price,
                    "Time": row.get("Market Time"),
                }
            )

    chart_df = pd.DataFrame(chart_rows).dropna(subset=["% From Open"])
    if chart_df.empty:
        return
    date_order = sorted(chart_df["Date"].unique(), reverse=True)
    chart_df["Date"] = pd.Categorical(chart_df["Date"], categories=date_order, ordered=True)
    chart_df = chart_df.sort_values(["Date", "Minutes From Open"])

    fig = px.line(
        chart_df,
        x="Minutes From Open",
        y="% From Open",
        color="Date",
        category_orders={"Date": date_order},
        title=f"{symbol}: First 2 Hours From Open",
        hover_data={"Time": True, "Price": ":.2f", "% From Open": ":.2f"},
    )
    fig.add_hline(y=0, line_dash="dot", line_color="#94a3b8")
    fig.update_xaxes(title="Minutes From Open", range=[0, 120])
    fig.update_yaxes(title="% From Open", ticksuffix="%")
    fig.update_layout(height=420, legend={"orientation": "h", "y": -0.25})
    st.plotly_chart(fig, use_container_width=True, key=f"first_two_hour_chart_{symbol}".lower())


def render_day_trade_10_day_analysis(symbols: list[str], api_key: str) -> None:
    st.subheader("Past 10 Trading Days: Low / High And Same-Day Windows")
    for symbol in symbols:
        range_df, first_two_hour_df = build_day_trade_range_tables(symbol, api_key)
        with st.expander(f"{symbol} 10-Day Day-Trade Range", expanded=len(symbols) == 1):
            if range_df.empty or first_two_hour_df.empty:
                st.info(f"No 10-day intraday analysis available for {symbol}.")
                continue

            render_first_two_hour_chart(symbol, api_key)

            pattern_summary = first_two_hour_df.copy()
            if not pattern_summary.empty:
                summary_col_1, summary_col_2, summary_col_3 = st.columns(3)
                most_common_pattern = pattern_summary["Pattern"].mode().iloc[0] if not pattern_summary["Pattern"].mode().empty else "-"
                avg_high_from_open = pattern_summary["High From Open"].mean()
                avg_recovery = pattern_summary["Recovery From Low"].mean()
                summary_col_1.metric("Common 2H Pattern", str(most_common_pattern))
                summary_col_2.metric("Avg 2H High From Open", format_percent(avg_high_from_open))
                summary_col_3.metric("Avg Recovery From Low", format_percent(avg_recovery))

            display_range_df = range_df.copy()
            for column in ["Low", "High", "Range"]:
                display_range_df[column] = display_range_df[column].map(format_money)
            display_range_df["Range %"] = display_range_df["Range %"].map(format_percent)
            display_range_df["Volume"] = display_range_df["Volume"].map(format_large_number)

            display_first_two_hour_df = first_two_hour_df.copy()
            for column in ["Open", "2H High", "2H Low", "2H Close"]:
                display_first_two_hour_df[column] = display_first_two_hour_df[column].map(format_money)
            for column in ["High From Open", "Dip From Open", "Recovery From Low", "2H Close From Open"]:
                display_first_two_hour_df[column] = display_first_two_hour_df[column].map(format_percent)

            st.markdown("**Daily Low / High**")
            st.dataframe(display_range_df, use_container_width=True, hide_index=True)
            st.markdown("**First 2 Hours From Open**")
            st.dataframe(display_first_two_hour_df, use_container_width=True, hide_index=True)


def render_day_trade_page(symbols: list[str], api_key: str) -> None:
    st.subheader("Day Trade Watch")
    if not api_key.strip():
        st.warning("Polygon.io API key is required for day-trade signals.")
        return

    unique_symbols = sorted({clean_symbol(symbol) for symbol in symbols if clean_symbol(symbol)})
    saved_day_trade_symbols = [
        clean_symbol(symbol)
        for symbol in str(st.query_params.get("day_trade_symbols", "")).split(",")
        if clean_symbol(symbol) in unique_symbols
    ]
    selected_symbols = st.multiselect(
        "Tickers",
        options=unique_symbols,
        default=saved_day_trade_symbols,
        key="day_trade_selected_tickers_v3",
        help="Pick the tickers you want to monitor for intraday range/trend/patterns.",
    )
    if selected_symbols:
        st.query_params["day_trade_symbols"] = ",".join(selected_symbols)
    elif "day_trade_symbols" in st.query_params:
        del st.query_params["day_trade_symbols"]
    if not selected_symbols:
        st.info("Select ticker(s) to scan. Nothing runs until you select.")
        return
    if len(selected_symbols) > 8:
        st.warning("Scanning many tickers may hit Polygon rate limits. Pick 5–8 tickers for best results.")

    rows = []
    progress = st.progress(0, text="Scanning intraday signals...")
    for index, symbol in enumerate(selected_symbols, start=1):
        rows.append(day_trade_signal_row(symbol, api_key))
        progress.progress(index / len(selected_symbols), text=f"Scanning {symbol} ({index}/{len(selected_symbols)})")
    progress.empty()

    signal_df = pd.DataFrame(rows)
    if signal_df.empty:
        st.info("No day-trade data found.")
        return

    ok_df = signal_df[signal_df["Status"] == "OK"].copy()
    if ok_df.empty:
        st.dataframe(signal_df, use_container_width=True, hide_index=True)
        return

    ok_df = ok_df.sort_values(["Score", "Range %", "Volume vs 30D Avg"], ascending=[False, False, False])
    display_df = ok_df.copy()
    for column in ["Price", "Day Low", "Day High"]:
        display_df[column] = display_df[column].map(format_money)
    for column in [
        "Range %",
        "Position In Range",
        "% From Open",
        "% From Prev Close",
        "5-Bar Trend",
        "20-Bar Trend",
        "Volume vs 30D Avg",
    ]:
        display_df[column] = display_df[column].map(format_percent)
    display_df["Volume"] = display_df["Volume"].map(format_large_number)

    metric_1, metric_2, metric_3, metric_4 = st.columns(4)
    metric_1.metric("Tickers", len(ok_df))
    metric_2.metric("Long Bias", int(ok_df["Signal"].isin(["Strong Long", "Long Bias"]).sum()))
    metric_3.metric("Positive Trend", int(ok_df["Patterns"].str.contains("Positive trend", regex=False).sum()))
    metric_4.metric("Opening Breakouts", int(ok_df["Patterns"].str.contains("Opening breakout", regex=False).sum()))

    def signal_style(value: object) -> str:
        if value == "Strong Long":
            return "color: #16a34a; font-weight: 800;"
        if value == "Long Bias":
            return "color: #15803d; font-weight: 700;"
        if value == "Weak / Avoid":
            return "color: #dc2626; font-weight: 700;"
        return ""

    def percent_style(value: object) -> str:
        text = str(value or "")
        if text.startswith("-"):
            return "color: #dc2626; font-weight: 700;"
        if text not in {"", "-"}:
            return "color: #16a34a; font-weight: 700;"
        return ""

    st.dataframe(
        display_df.drop(columns=["Status"]).style.map(signal_style, subset=["Signal"]).map(
            percent_style,
            subset=["% From Open", "% From Prev Close", "5-Bar Trend", "20-Bar Trend"],
        ),
        use_container_width=True,
        hide_index=True,
    )
    render_day_trade_10_day_analysis(selected_symbols, api_key)


def render_moving_average_controls(symbol: str) -> list[int]:
    st.markdown("**Moving Averages**")
    selected_windows = []
    for window in MOVING_AVERAGE_WINDOWS:
        if st.checkbox(
            f"{window}D",
            value=False,
            key=f"moving_average_{symbol}_{window}",
        ):
            selected_windows.append(window)
    return selected_windows


def add_volume_average_line(fig: go.Figure, symbol: str, volume_df: pd.DataFrame, api_key: str) -> None:
    try:
        daily_df = fetch_daily_history(symbol, api_key)
    except Exception:
        return
    if daily_df.empty or "Volume MA30" not in daily_df.columns:
        return

    min_chart_time = pd.to_datetime(volume_df["Datetime"]).min()
    max_chart_time = pd.to_datetime(volume_df["Datetime"]).max()
    visible_daily_df = daily_df[
        (pd.to_datetime(daily_df["Datetime"]) >= min_chart_time)
        & (pd.to_datetime(daily_df["Datetime"]) <= max_chart_time)
    ].copy()
    if visible_daily_df.empty:
        return

    fig.add_trace(
        go.Scatter(
            x=visible_daily_df["Datetime"],
            y=visible_daily_df["Volume MA30"],
            mode="lines",
            name="30D Avg Volume",
            line={"color": "#a855f7", "width": 2.4},
            yaxis="y",
        )
    )


def format_money(value: object) -> str:
    number = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(number):
        return "-"
    return f"${number:,.2f}"


def format_percent(value: object) -> str:
    number = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(number):
        return "-"
    return f"{number:,.2f}%"


def render_price_header(symbol: str, current_price: object, change_value: object, change_label: str = "") -> None:
    current_number = numeric_value(current_price)
    change_number = numeric_value(change_value)
    previous_close = current_number - change_number if not pd.isna(current_number) and not pd.isna(change_number) else math.nan
    change_percent = (change_number / previous_close * 100) if previous_close and not pd.isna(previous_close) else math.nan
    change_color = "#16a34a" if not pd.isna(change_number) and change_number >= 0 else "#dc2626"
    change_prefix = "+" if not pd.isna(change_number) and change_number > 0 else ""
    change_text = (
        f"{change_prefix}{change_number:,.2f} ({change_prefix}{change_percent:,.2f}%)"
        if not pd.isna(change_number) and not pd.isna(change_percent)
        else "-"
    )
    label_text = f" {change_label}" if change_label else ""
    st.markdown(
        f"""
        <div style="margin-bottom: 0.75rem;">
          <div style="font-size: 2rem; font-weight: 700; line-height: 1.1;">{symbol}</div>
          <div style="display: flex; align-items: baseline; gap: 0.75rem; margin-top: 0.35rem;">
            <span style="font-size: 2.35rem; font-weight: 700;">{format_money(current_price)}</span>
            <span style="font-size: 1.15rem; font-weight: 700; color: {change_color};">{change_text}{label_text}</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def format_large_number(value: object) -> str:
    number = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(number):
        return "-"
    abs_number = abs(float(number))
    if abs_number >= 1_000_000_000_000:
        return f"{number / 1_000_000_000_000:.3f}T"
    if abs_number >= 1_000_000_000:
        return f"{number / 1_000_000_000:.3f}B"
    if abs_number >= 1_000_000:
        return f"{number / 1_000_000:.3f}M"
    return f"{number:,.0f}"


def format_range(low_value: object, high_value: object) -> str:
    low_number = pd.to_numeric(pd.Series([low_value]), errors="coerce").iloc[0]
    high_number = pd.to_numeric(pd.Series([high_value]), errors="coerce").iloc[0]
    if pd.isna(low_number) or pd.isna(high_number):
        return "-"
    return f"{low_number:,.2f} - {high_number:,.2f}"


def numeric_value(value: object) -> float:
    return pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]


def parse_money_value(value: object) -> float:
    cleaned = str(value or "").replace("$", "").replace(",", "").strip()
    if cleaned in {"", "-", "--", "nan", "None"}:
        return math.nan
    return numeric_value(cleaned)


def parse_large_money_value(value: object) -> float:
    cleaned = str(value or "").replace("$", "").replace(",", "").strip().upper()
    if cleaned in {"", "-", "--", "NAN", "NONE"}:
        return math.nan
    multiplier = 1.0
    if cleaned.endswith("T"):
        multiplier = 1_000_000_000_000
        cleaned = cleaned[:-1]
    elif cleaned.endswith("B"):
        multiplier = 1_000_000_000
        cleaned = cleaned[:-1]
    elif cleaned.endswith("M"):
        multiplier = 1_000_000
        cleaned = cleaned[:-1]
    return numeric_value(cleaned) * multiplier


def first_valid_value(*values: object) -> object:
    for value in values:
        number = numeric_value(value)
        if not pd.isna(number):
            return value
    return math.nan


def is_us_market_hours() -> bool:
    now_et = datetime.now(ZoneInfo("America/New_York"))
    if now_et.weekday() >= 5:
        return False
    market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= now_et <= market_close


def enable_live_refresh() -> None:
    if not is_us_market_hours():
        return
    components.html(
        """
        <script>
          const key = "finviz-scroll-y";
          const restore = () => {
            const saved = window.parent.sessionStorage.getItem(key);
            if (saved !== null) {
              window.parent.scrollTo(0, Number(saved) || 0);
            }
          };
          restore();
          setTimeout(() => {
            window.parent.sessionStorage.setItem(key, String(window.parent.scrollY || 0));
            window.parent.location.reload();
          }, 60000);
        </script>
        """,
        height=0,
    )


def render_header() -> None:
    st.title("📈 Gan Portfolio Dashboard")
    st.caption(
        "Yahoo Finance-style portfolio navigator with watchlist, holdings, Polygon.io price charts, and quick performance views. "
        "For personal tracking only — not investment advice."
    )


def render_portfolio_metrics(df: pd.DataFrame) -> None:
    holdings = df[df["Quantity"].fillna(0) > 0].copy() if "Quantity" in df.columns else pd.DataFrame()
    watchlist_count = len(df)
    holding_count = len(holdings)
    total_value = float(holdings["Market Value"].sum()) if not holdings.empty else 0.0
    total_gain = float(holdings["Gain/Loss"].sum()) if not holdings.empty else 0.0

    metric_1, metric_2, metric_3, metric_4 = st.columns(4)
    metric_1.metric("Symbols", f"{watchlist_count:,}")
    metric_2.metric("Holdings With Qty", f"{holding_count:,}")
    metric_3.metric("Tracked Market Value", format_money(total_value))
    metric_4.metric("Tracked Gain/Loss", format_money(total_gain))


def render_allocation_chart(df: pd.DataFrame, key: str) -> None:
    holdings = df[df["Market Value"].fillna(0) > 0].copy()
    if holdings.empty:
        st.info("Add Quantity values in the CSV to unlock allocation charts.")
        return
    fig = px.pie(
        holdings,
        names="Symbol",
        values="Market Value",
        title="Allocation By Market Value",
        hole=0.42,
    )
    st.plotly_chart(fig, use_container_width=True, key=key)


def render_watchlist_table(df: pd.DataFrame) -> None:
    display_df = df.copy()
    if "Symbol" in display_df.columns:
        display_df["Chart"] = display_df["Symbol"].map(
            lambda symbol: f"?page=Charts&symbol={quote(str(symbol), safe='')}"
        )
    display_columns = [
        column
        for column in [
            "Chart",
            "Symbol",
            "Current Price",
            "Change",
            "Price Source",
            "Open",
            "High",
            "Low",
            "Volume",
            "Purchase Price",
            "Quantity",
            "Market Value",
            "Gain/Loss",
            "Gain/Loss %",
            "Comment",
        ]
        if column in df.columns
    ]
    if "Chart" in display_df.columns and "Chart" not in display_columns:
        display_columns.insert(0, "Chart")
    st.dataframe(
        display_df[display_columns],
        use_container_width=True,
        hide_index=True,
        column_config={
            "Chart": st.column_config.LinkColumn("Chart", display_text="Open"),
            "Current Price": st.column_config.NumberColumn("Current Price", format="$%.2f"),
            "Change": st.column_config.NumberColumn("Change", format="%.2f"),
            "Open": st.column_config.NumberColumn("Open", format="$%.2f"),
            "High": st.column_config.NumberColumn("High", format="$%.2f"),
            "Low": st.column_config.NumberColumn("Low", format="$%.2f"),
            "Volume": st.column_config.NumberColumn("Volume", format="%d"),
            "Purchase Price": st.column_config.NumberColumn("Purchase Price", format="$%.2f"),
            "Quantity": st.column_config.NumberColumn("Quantity", format="%.4f"),
            "Market Value": st.column_config.NumberColumn("Market Value", format="$%.2f"),
            "Gain/Loss": st.column_config.NumberColumn("Gain/Loss", format="$%.2f"),
            "Gain/Loss %": st.column_config.NumberColumn("Gain/Loss %", format="%.2f%%"),
        },
    )


def comparable_symbol_rows(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(path, encoding="utf-8-sig", index_col=False)
    except Exception:
        return pd.DataFrame()
    if df.empty or "Symbol" not in df.columns:
        return pd.DataFrame()
    df.columns = [str(column).strip() for column in df.columns]
    df = df.copy()
    df["Symbol"] = df["Symbol"].map(clean_symbol)
    rename_map = {
        "Last Price": "Current Price",
        "Current Value": "Market Value",
        "Average Cost Basis": "Purchase Price",
        "Cost Basis Total": "Cost Basis",
        "Total Gain/Loss Dollar": "Gain/Loss",
        "Total Gain/Loss Percent": "Gain/Loss %",
    }
    for source, target in rename_map.items():
        if source in df.columns and target not in df.columns:
            df[target] = df[source]
    date_acquired_columns = ["Date Acquired", "Acquired Date", "Purchase Date", "Date Purchased", "Open Date"]
    if "Date Acquired" not in df.columns:
        source_column = next((column for column in date_acquired_columns if column in df.columns), "")
        df["Date Acquired"] = df[source_column] if source_column else "-"
    for column in ["Current Price", "Quantity", "Market Value", "Purchase Price", "Cost Basis", "Gain/Loss", "Gain/Loss %"]:
        if column in df.columns:
            df[column] = parse_numeric_series(df[column])
    df = df[
        (df["Symbol"] != "")
        & (~df["Symbol"].str.contains(r"\*", regex=True))
        & (~df["Symbol"].str.endswith("XX"))
    ].copy()
    return df.drop_duplicates(subset=["Symbol"], keep="first").reset_index(drop=True)


def xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        xml_bytes = archive.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ET.fromstring(xml_bytes)
    namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    values = []
    for item in root.findall("x:si", namespace):
        text = "".join(node.text or "" for node in item.findall(".//x:t", namespace))
        values.append(text)
    return values


def xlsx_cell_value(cell: ET.Element, shared_strings: list[str]) -> str:
    namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    cell_type = cell.attrib.get("t", "")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(".//x:t", namespace)).strip()
    value_node = cell.find("x:v", namespace)
    if value_node is None or value_node.text is None:
        return ""
    raw_value = value_node.text.strip()
    if cell_type == "s":
        try:
            return shared_strings[int(raw_value)].strip()
        except (ValueError, IndexError):
            return ""
    return raw_value


def xlsx_column_index(cell_ref: str) -> int:
    letters = re.sub(r"[^A-Z]", "", cell_ref.upper())
    index = 0
    for letter in letters:
        index = index * 26 + ord(letter) - ord("A") + 1
    return max(index - 1, 0)


def read_xlsx_first_sheet(path: Path) -> pd.DataFrame:
    try:
        with zipfile.ZipFile(path) as archive:
            shared_strings = xlsx_shared_strings(archive)
            worksheet_name = next(
                name
                for name in archive.namelist()
                if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
            )
            root = ET.fromstring(archive.read(worksheet_name))
    except Exception:
        return pd.DataFrame()

    namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rows = []
    for row in root.findall(".//x:sheetData/x:row", namespace):
        values = []
        for cell in row.findall("x:c", namespace):
            index = xlsx_column_index(cell.attrib.get("r", ""))
            while len(values) <= index:
                values.append("")
            values[index] = xlsx_cell_value(cell, shared_strings)
        if any(str(value).strip() for value in values):
            rows.append(values)
    if not rows:
        return pd.DataFrame()
    header_index = next(
        (
            index
            for index, row in enumerate(rows)
            if {"Date", "Description", "Symbol"}.issubset({str(value).strip() for value in row})
        ),
        0,
    )
    header = [str(value).strip() for value in rows[header_index]]
    data_rows = []
    for row in rows[header_index + 1 :]:
        padded_row = row + [""] * max(len(header) - len(row), 0)
        data_rows.append(padded_row[: len(header)])
    return pd.DataFrame(data_rows, columns=header)


def parse_activity_date(value: object) -> pd.Timestamp:
    if value is None or str(value).strip() == "":
        return pd.NaT
    parsed = pd.to_datetime(str(value).strip(), errors="coerce")
    if pd.notna(parsed):
        return parsed
    numeric_value = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(numeric_value):
        return pd.NaT
    return pd.to_datetime("1899-12-30") + pd.to_timedelta(float(numeric_value), unit="D")


def activity_transactions(pattern: str) -> pd.DataFrame:
    frames = []
    for path in sorted(Path(".").glob(pattern)):
        df = read_xlsx_first_sheet(path)
        if df.empty or "Symbol" not in df.columns or "Description" not in df.columns:
            continue
        df = df.copy()
        df["Symbol"] = df["Symbol"].map(clean_symbol)
        df["Description"] = df["Description"].astype(str)
        description_upper = df["Description"].str.upper()
        df = df[~description_upper.str.contains("CANCEL", na=False)].copy()
        df["Action"] = ""
        df.loc[description_upper.str.contains("YOU BOUGHT", na=False), "Action"] = "Acquired"
        df.loc[description_upper.str.contains("YOU SOLD", na=False), "Action"] = "Sold"
        df = df[df["Action"] != ""].copy()
        if df.empty:
            continue
        df["Activity Date"] = df["Date"].map(parse_activity_date) if "Date" in df.columns else pd.NaT
        if "Price" in df.columns:
            df["Activity Price"] = parse_numeric_series(df["Price"])
        else:
            df["Activity Price"] = pd.NA
        frames.append(df[["Symbol", "Action", "Activity Date", "Activity Price"]])
    if not frames:
        return pd.DataFrame(columns=["Symbol", "Action", "Activity Date", "Activity Price"])
    activity_df = pd.concat(frames, ignore_index=True)
    return activity_df[(activity_df["Symbol"] != "") & activity_df["Activity Date"].notna()].copy()


def latest_activity_summary(activity_df: pd.DataFrame, account: str) -> pd.DataFrame:
    acquired_date_column = f"{account} Last Acquired"
    acquired_price_column = f"{account} Acquired Price"
    sold_date_column = f"{account} Last Sold"
    sold_price_column = f"{account} Sold Price"
    if activity_df.empty:
        return pd.DataFrame(
            columns=[
                "Symbol",
                acquired_date_column,
                acquired_price_column,
                sold_date_column,
                sold_price_column,
            ]
        )
    rows = []
    for symbol, symbol_df in activity_df.sort_values("Activity Date").groupby("Symbol"):
        acquired = symbol_df[symbol_df["Action"] == "Acquired"].tail(1)
        sold = symbol_df[symbol_df["Action"] == "Sold"].tail(1)
        acquired_row = acquired.iloc[0] if not acquired.empty else {}
        sold_row = sold.iloc[0] if not sold.empty else {}
        rows.append(
            {
                "Symbol": symbol,
                acquired_date_column: acquired_row.get("Activity Date", pd.NaT),
                acquired_price_column: acquired_row.get("Activity Price", pd.NA),
                sold_date_column: sold_row.get("Activity Date", pd.NaT),
                sold_price_column: sold_row.get("Activity Price", pd.NA),
            }
        )
    summary_df = pd.DataFrame(rows)
    for column in [acquired_date_column, sold_date_column]:
        summary_df[column] = pd.to_datetime(summary_df[column], errors="coerce").dt.strftime("%Y-%m-%d").fillna("-")
    return summary_df


def attach_account_activity(df: pd.DataFrame, activity_summary: pd.DataFrame, account: str) -> pd.DataFrame:
    if df.empty or activity_summary.empty:
        return df
    merged = df.merge(activity_summary, on="Symbol", how="left")
    if "Date Acquired" in merged.columns:
        missing_date = merged["Date Acquired"].astype(str).isin(["", "-", "nan", "NaT"])
        acquired_date_column = f"{account} Last Acquired"
        if acquired_date_column in merged.columns:
            merged.loc[missing_date, "Date Acquired"] = merged.loc[missing_date, acquired_date_column].fillna("-")
    return merged


def compare_date_value(*values: object) -> object:
    for value in values:
        if value is None or pd.isna(value):
            continue
        text = str(value).strip()
        if text and text not in {"-", "nan", "NaT"}:
            return text
    return "-"


def compare_portfolios(raju_df: pd.DataFrame, padmaja_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    raju_symbols = set(raju_df["Symbol"].dropna().map(clean_symbol)) if not raju_df.empty else set()
    padmaja_symbols = set(padmaja_df["Symbol"].dropna().map(clean_symbol)) if not padmaja_df.empty else set()

    buy_symbols = sorted(padmaja_symbols - raju_symbols)
    sell_symbols = sorted(raju_symbols - padmaja_symbols)
    common_symbols = sorted(raju_symbols & padmaja_symbols)

    buy_df = padmaja_df[padmaja_df["Symbol"].isin(buy_symbols)].copy()
    sell_df = raju_df[raju_df["Symbol"].isin(sell_symbols)].copy()
    raju_total_value = raju_df["Market Value"].sum(skipna=True) if "Market Value" in raju_df.columns else 0
    padmaja_total_value = padmaja_df["Market Value"].sum(skipna=True) if "Market Value" in padmaja_df.columns else 0

    common_rows = []
    for symbol in common_symbols:
        raju_row = raju_df[raju_df["Symbol"] == symbol].iloc[0].to_dict()
        padmaja_row = padmaja_df[padmaja_df["Symbol"] == symbol].iloc[0].to_dict()
        raju_value = raju_row.get("Market Value")
        padmaja_value = padmaja_row.get("Market Value")
        common_rows.append(
            {
                "Symbol": symbol,
                "Raju Quantity": raju_row.get("Quantity"),
                "Padmaja Quantity": padmaja_row.get("Quantity"),
                "Raju Value": raju_value,
                "Raju Weight": (raju_value / raju_total_value * 100) if raju_total_value else pd.NA,
                "Raju Gain/Loss %": raju_row.get("Gain/Loss %"),
                "Raju Acquired Date": compare_date_value(raju_row.get("Raju Last Acquired"), raju_row.get("Date Acquired")),
                "Padmaja Value": padmaja_value,
                "Padmaja Weight": (padmaja_value / padmaja_total_value * 100) if padmaja_total_value else pd.NA,
                "Padmaja Gain/Loss %": padmaja_row.get("Gain/Loss %"),
                "Padmaja Acquired Date": compare_date_value(
                    padmaja_row.get("Padmaja Last Acquired"),
                    padmaja_row.get("Date Acquired"),
                ),
                "Raju Avg Cost": raju_row.get("Purchase Price"),
                "Padmaja Avg Cost": padmaja_row.get("Purchase Price"),
            }
        )
    return buy_df, sell_df, pd.DataFrame(common_rows)


def render_portfolio_compare_table(df: pd.DataFrame, action: str) -> None:
    if df.empty:
        st.success(f"No {action} candidates.")
        return
    display_columns = [
        column
        for column in [
            "Symbol",
            "Raju Last Acquired",
            "Raju Acquired Price",
            "Raju Last Sold",
            "Raju Sold Price",
            "Padmaja Last Acquired",
            "Padmaja Acquired Price",
            "Padmaja Last Sold",
            "Padmaja Sold Price",
            "Current Price",
            "Gain/Loss",
            "Gain/Loss %",
            "Quantity",
            "Market Value",
            "Purchase Price",
            "Comment",
        ]
        if column in df.columns
    ]
    display_df = df[display_columns].copy()
    render_selectable_compare_dataframe(display_df, f"compare_{action}")


def all_compare_positions(raju_df: pd.DataFrame, padmaja_df: pd.DataFrame) -> pd.DataFrame:
    frames = []
    for account, source_df in [("Raju", raju_df), ("Padmaja", padmaja_df)]:
        if source_df.empty:
            continue
        df = source_df.copy()
        df["Account"] = account
        if "Market Value" not in df.columns:
            df["Market Value"] = pd.NA
        if "Current Price" not in df.columns:
            df["Current Price"] = pd.NA
        if "Quantity" not in df.columns:
            df["Quantity"] = pd.NA
        if "Purchase Price" not in df.columns:
            df["Purchase Price"] = pd.NA
        account_acquired_price_column = f"{account} Acquired Price"
        if account_acquired_price_column in df.columns:
            acquired_price = df[account_acquired_price_column].combine_first(df["Purchase Price"])
        else:
            acquired_price = df["Purchase Price"]
        df["Acquired Price"] = acquired_price
        for column in ["Current Price", "Acquired Price", "Quantity", "Market Value", "Gain/Loss %"]:
            if column in df.columns:
                df[column] = pd.to_numeric(df[column], errors="coerce")
        missing_market_value = df["Market Value"].isna()
        df.loc[missing_market_value, "Market Value"] = (
            df.loc[missing_market_value, "Current Price"] * df.loc[missing_market_value, "Quantity"]
        )
        if "Gain/Loss %" not in df.columns:
            df["Gain/Loss %"] = pd.NA
        missing_gain_percent = df["Gain/Loss %"].isna() & df["Current Price"].notna() & df["Acquired Price"].gt(0)
        if missing_gain_percent.any():
            df.loc[missing_gain_percent, "Gain/Loss %"] = (
                (df.loc[missing_gain_percent, "Current Price"] - df.loc[missing_gain_percent, "Acquired Price"])
                / df.loc[missing_gain_percent, "Acquired Price"]
                * 100
            )
        total_value = df["Market Value"].sum(skipna=True)
        df["% of Total"] = (df["Market Value"] / total_value * 100) if total_value else pd.NA
        frames.append(
            df[
                [
                    "Account",
                    "Symbol",
                    "Current Price",
                    "Acquired Price",
                    "Quantity",
                    "Gain/Loss %",
                    "Market Value",
                    "% of Total",
                ]
            ]
        )
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).sort_values(["Symbol", "Account"]).reset_index(drop=True)


def render_selectable_compare_dataframe(df: pd.DataFrame, key: str) -> None:
    if df.empty:
        st.info("No rows.")
        return

    search_text = st.text_input(
        "Search/filter rows",
        key=f"{key}_search",
        placeholder="Type symbol, account, date, etc.",
    ).strip()
    display_df = df.copy()
    if search_text:
        search_mask = display_df.apply(
            lambda row: row.astype(str).str.contains(search_text, case=False, na=False).any(),
            axis=1,
        )
        display_df = display_df[search_mask].copy()
        st.caption(f"Showing {len(display_df)} of {len(df)} rows")
    display_df = display_df.reset_index(drop=True)

    button_col_1, button_col_2 = st.columns([1, 5])
    with button_col_1:
        if st.button("Show selected only", key=f"{key}_show_selected_button"):
            st.session_state[f"{key}_show_selected_only"] = True
    with button_col_2:
        if st.button("Hide selected view", key=f"{key}_hide_selected_button"):
            st.session_state[f"{key}_show_selected_only"] = False

    selected_rows = []
    table_state = st.session_state.get(f"{key}_table", {})
    edited_rows = table_state.get("edited_rows", {}) if isinstance(table_state, dict) else {}
    for row_index, changes in edited_rows.items():
        try:
            row_position = int(row_index)
        except (TypeError, ValueError):
            continue
        if changes.get("Select") and row_position < len(display_df):
            selected_rows.append(row_position)
    selected_preview_df = display_df.iloc[selected_rows].copy() if selected_rows else pd.DataFrame()

    if st.session_state.get(f"{key}_show_selected_only", False):
        if selected_preview_df.empty:
            st.warning("No rows selected. Check rows in the `Select` column first.")
        else:
            st.markdown("**Selected Rows**")
            st.dataframe(
                selected_preview_df,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Current Price": st.column_config.NumberColumn("Current Price", format="$%.2f"),
                    "Acquired Price": st.column_config.NumberColumn("Acquired Price", format="$%.2f"),
                    "Purchase Price": st.column_config.NumberColumn("Purchase Price", format="$%.2f"),
                    "Market Value": st.column_config.NumberColumn("Market Value", format="$%.2f"),
                    "Gain/Loss": st.column_config.NumberColumn("Gain/Loss", format="$%.2f"),
                    "Gain/Loss %": st.column_config.NumberColumn("Gain/Loss %", format="%.2f%%"),
                    "Quantity": st.column_config.NumberColumn("Quantity", format="%.4f"),
                    "Raju Quantity": st.column_config.NumberColumn("Raju Quantity", format="%.4f"),
                    "Padmaja Quantity": st.column_config.NumberColumn("Padmaja Quantity", format="%.4f"),
                    "Raju Value": st.column_config.NumberColumn("Raju Value", format="$%.2f"),
                    "Padmaja Value": st.column_config.NumberColumn("Padmaja Value", format="$%.2f"),
                    "Raju Weight": st.column_config.NumberColumn("Raju Weight", format="%.2f%%"),
                    "Padmaja Weight": st.column_config.NumberColumn("Padmaja Weight", format="%.2f%%"),
                    "Raju Gain/Loss %": st.column_config.NumberColumn("Raju Gain/Loss %", format="%.2f%%"),
                    "Padmaja Gain/Loss %": st.column_config.NumberColumn("Padmaja Gain/Loss %", format="%.2f%%"),
                    "Raju Avg Cost": st.column_config.NumberColumn("Raju Avg Cost", format="$%.2f"),
                    "Padmaja Avg Cost": st.column_config.NumberColumn("Padmaja Avg Cost", format="$%.2f"),
                    "% of Total": st.column_config.NumberColumn("% of Total", format="%.2f%%"),
                    "Raju Acquired Price": st.column_config.NumberColumn("Raju Acquired Price", format="$%.2f"),
                    "Raju Sold Price": st.column_config.NumberColumn("Raju Sold Price", format="$%.2f"),
                    "Padmaja Acquired Price": st.column_config.NumberColumn("Padmaja Acquired Price", format="$%.2f"),
                    "Padmaja Sold Price": st.column_config.NumberColumn("Padmaja Sold Price", format="$%.2f"),
                },
            )

    editor_df = display_df.copy()
    editor_df.insert(0, "Select", False)
    st.data_editor(
        editor_df,
        use_container_width=True,
        hide_index=True,
        key=f"{key}_table",
        disabled=[column for column in editor_df.columns if column != "Select"],
        column_config={
            "Select": st.column_config.CheckboxColumn("Select"),
            "Current Price": st.column_config.NumberColumn("Current Price", format="$%.2f"),
            "Acquired Price": st.column_config.NumberColumn("Acquired Price", format="$%.2f"),
            "Purchase Price": st.column_config.NumberColumn("Purchase Price", format="$%.2f"),
            "Market Value": st.column_config.NumberColumn("Market Value", format="$%.2f"),
            "Gain/Loss": st.column_config.NumberColumn("Gain/Loss", format="$%.2f"),
            "Gain/Loss %": st.column_config.NumberColumn("Gain/Loss %", format="%.2f%%"),
            "Quantity": st.column_config.NumberColumn("Quantity", format="%.4f"),
            "Raju Quantity": st.column_config.NumberColumn("Raju Quantity", format="%.4f"),
            "Padmaja Quantity": st.column_config.NumberColumn("Padmaja Quantity", format="%.4f"),
            "Raju Value": st.column_config.NumberColumn("Raju Value", format="$%.2f"),
            "Padmaja Value": st.column_config.NumberColumn("Padmaja Value", format="$%.2f"),
            "Raju Weight": st.column_config.NumberColumn("Raju Weight", format="%.2f%%"),
            "Padmaja Weight": st.column_config.NumberColumn("Padmaja Weight", format="%.2f%%"),
            "Raju Gain/Loss %": st.column_config.NumberColumn("Raju Gain/Loss %", format="%.2f%%"),
            "Padmaja Gain/Loss %": st.column_config.NumberColumn("Padmaja Gain/Loss %", format="%.2f%%"),
            "Raju Avg Cost": st.column_config.NumberColumn("Raju Avg Cost", format="$%.2f"),
            "Padmaja Avg Cost": st.column_config.NumberColumn("Padmaja Avg Cost", format="$%.2f"),
            "% of Total": st.column_config.NumberColumn("% of Total", format="%.2f%%"),
            "Raju Acquired Price": st.column_config.NumberColumn("Raju Acquired Price", format="$%.2f"),
            "Raju Sold Price": st.column_config.NumberColumn("Raju Sold Price", format="$%.2f"),
            "Padmaja Acquired Price": st.column_config.NumberColumn("Padmaja Acquired Price", format="$%.2f"),
            "Padmaja Sold Price": st.column_config.NumberColumn("Padmaja Sold Price", format="$%.2f"),
        },
    )


def render_all_compare_positions_table(raju_df: pd.DataFrame, padmaja_df: pd.DataFrame) -> None:
    positions_df = all_compare_positions(raju_df, padmaja_df)
    if positions_df.empty:
        st.info("No positions found.")
        return
    render_selectable_compare_dataframe(positions_df, "compare_all_positions")


def render_raju_padmaja_compare_page(api_key: str) -> None:
    st.subheader("Raju vs Padmaja")
    raju_df = comparable_symbol_rows(RAJU_COMPARE_PATH)
    padmaja_df = comparable_symbol_rows(PADMAJA_COMPARE_PATH)
    if raju_df.empty or padmaja_df.empty:
        st.warning(f"Need `{RAJU_COMPARE_PATH}` and `{PADMAJA_COMPARE_PATH}` in this repo folder.")
        return

    raju_df = apply_live_ticker_prices(raju_df, api_key)
    padmaja_df = apply_live_ticker_prices(padmaja_df, api_key)
    raju_activity_summary = latest_activity_summary(activity_transactions(RAJU_ACTIVITY_PATTERN), "Raju")
    padmaja_activity_summary = latest_activity_summary(activity_transactions(PADMAJA_ACTIVITY_PATTERN), "Padmaja")
    raju_df = attach_account_activity(raju_df, raju_activity_summary, "Raju")
    padmaja_df = attach_account_activity(padmaja_df, padmaja_activity_summary, "Padmaja")
    buy_df, sell_df, common_df = compare_portfolios(raju_df, padmaja_df)
    metric_1, metric_2, metric_3, metric_4 = st.columns(4)
    metric_1.metric("Raju Symbols", len(raju_df))
    metric_2.metric("Padmaja Symbols", len(padmaja_df))
    metric_3.metric("Buy Candidates", len(buy_df))
    metric_4.metric("Sell Candidates", len(sell_df))

    buy_tab, sell_tab, common_tab, all_tab = st.tabs(["Buy For Raju", "Sell From Raju", "In Both", "All Positions"])
    with buy_tab:
        st.caption("In Padmaja but not in Raju — buy candidates for Raju.")
        render_portfolio_compare_table(buy_df.sort_values("Symbol"), "buy")
    with sell_tab:
        st.caption("In Raju but not in Padmaja — sell candidates from Raju.")
        render_portfolio_compare_table(sell_df.sort_values("Symbol"), "sell")
    with common_tab:
        if common_df.empty:
            st.info("No shared symbols.")
        else:
            common_display_columns = [
                "Symbol",
                "Raju Quantity",
                "Raju Value",
                "Raju Weight",
                "Raju Gain/Loss %",
                "Raju Acquired Date",
                "Raju Avg Cost",
                "Padmaja Quantity",
                "Padmaja Value",
                "Padmaja Weight",
                "Padmaja Gain/Loss %",
                "Padmaja Acquired Date",
                "Padmaja Avg Cost",
            ]
            common_display_df = common_df[
                [column for column in common_display_columns if column in common_df.columns]
            ].sort_values("Symbol")
            render_selectable_compare_dataframe(common_display_df, "compare_in_both")
    with all_tab:
        st.caption("All Raju and Padmaja positions, sorted by symbol. `% of Total` is calculated within each account.")
        render_all_compare_positions_table(raju_df, padmaja_df)


def render_quote_stats_table(
    symbol: str,
    chart_df: pd.DataFrame,
    meta: Dict[str, object],
    earnings: Dict[str, object],
    api_key: str,
    snapshot: Optional[Dict[str, object]] = None,
    quote_record: Optional[Dict[str, object]] = None,
) -> None:
    snapshot = snapshot or {}
    quote_record = quote_record or {}
    latest_row = chart_df.dropna(subset=["Close"]).iloc[-1]
    snapshot_day = snapshot.get("day") if isinstance(snapshot.get("day"), dict) else {}
    snapshot_prev_day = snapshot.get("prevDay") if isinstance(snapshot.get("prevDay"), dict) else {}
    current_price = first_valid_value(
        snapshot_current_price(snapshot),
        quote_record.get("Current Price"),
        latest_row.get("Close"),
    )
    latest_open = first_valid_value(snapshot_day.get("o"), quote_record.get("Open"), latest_row.get("Open"))
    latest_high = first_valid_value(snapshot_day.get("h"), quote_record.get("High"), latest_row.get("High"))
    latest_low = first_valid_value(snapshot_day.get("l"), quote_record.get("Low"), latest_row.get("Low"))
    latest_volume = first_valid_value(snapshot_day.get("v"), quote_record.get("Volume"), latest_row.get("Volume"))
    quote_change = numeric_value(quote_record.get("Change"))
    current_price_number = numeric_value(current_price)
    snapshot_previous_close = first_valid_value(meta.get("chartPreviousClose"), snapshot_prev_day.get("c"))
    csv_previous_close = (
        current_price_number - quote_change
        if not pd.isna(current_price_number) and not pd.isna(quote_change)
        else math.nan
    )
    previous_close = first_valid_value(snapshot_previous_close, csv_previous_close)

    daily_df = fetch_daily_history(symbol, api_key)
    if daily_df.empty:
        avg_volume = math.nan
        week_52_range = "-"
        latest_ma_values = {window: math.nan for window in MOVING_AVERAGE_WINDOWS}
    else:
        avg_volume = pd.to_numeric(daily_df["Volume"], errors="coerce").tail(30).mean() if "Volume" in daily_df.columns else math.nan
        recent_year_df = daily_df.tail(min(len(daily_df), 252))
        week_52_range = (
            format_range(recent_year_df["Low"].min(), recent_year_df["High"].max())
            if {"Low", "High"}.issubset(recent_year_df.columns)
            else "-"
        )
        latest_ma_row = daily_df.dropna(subset=["Datetime"]).sort_values("Datetime").tail(1)
        latest_ma_values = {
            window: numeric_value(latest_ma_row[f"MA{window}"].iloc[0])
            if not latest_ma_row.empty and f"MA{window}" in latest_ma_row.columns
            else math.nan
            for window in MOVING_AVERAGE_WINDOWS
        }

    ticker_details = fetch_ticker_details(symbol, api_key)
    fundamentals = fetch_fundamentals(symbol)
    market_cap = ticker_details.get("market_cap") or ticker_details.get("weighted_shares_outstanding")
    stats = [
        ("Previous Close", format_money(previous_close)),
        ("Day's Range", format_range(latest_low, latest_high)),
        ("Market Cap", format_large_number(market_cap)),
        ("Earnings Date", earnings_label(earnings)),
        ("Open", format_money(latest_open)),
        ("52 Week Range", week_52_range),
        ("Volume", format_large_number(latest_volume)),
        ("Avg. Volume", format_large_number(avg_volume)),
        ("PE Ratio (TTM)", fundamentals.get("pe_ratio", "-")),
        ("EPS (TTM)", fundamentals.get("eps_ttm", "-")),
        ("Beta", fundamentals.get("beta", "-")),
        ("1Y Target Est", fundamentals.get("target_price", "-")),
        ("MA10", format_money(latest_ma_values.get(10))),
        ("MA20", format_money(latest_ma_values.get(20))),
        ("MA50", format_money(latest_ma_values.get(50))),
        ("MA200", format_money(latest_ma_values.get(200))),
    ]

    cells = "".join(
        f"""
        <div class="quote-stat">
          <span class="quote-label">{label}</span>
          <span class="quote-value">{value}</span>
        </div>
        """
        for label, value in stats
    )
    st.markdown(
        f"""
        <style>
          .quote-grid {{
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            border: 1px solid #e5e7eb;
            border-radius: 12px;
            overflow: hidden;
            margin: 0.75rem 0 1rem 0;
          }}
          .quote-stat {{
            display: flex;
            justify-content: space-between;
            gap: 1rem;
            padding: 0.7rem 0.9rem;
            border-bottom: 1px solid #e5e7eb;
            border-right: 1px solid #e5e7eb;
            background: #ffffff;
          }}
          .quote-stat:nth-last-child(-n+4) {{
            border-bottom: 0;
          }}
          .quote-stat:nth-child(4n) {{
            border-right: 0;
          }}
          .quote-label {{
            color: #374151;
            font-weight: 500;
          }}
          .quote-value {{
            color: #111827;
            font-weight: 700;
            text-align: right;
          }}
        </style>
        <div class="quote-grid">{cells}</div>
        """,
        unsafe_allow_html=True,
    )


def render_analyst_projections(symbol: str, current_price: object) -> None:
    fundamentals = fetch_fundamentals(symbol)
    target_price = numeric_value(fundamentals.get("target_price"))
    current_price_number = numeric_value(current_price)
    upside = (
        (target_price / current_price_number - 1) * 100
        if not pd.isna(target_price) and not pd.isna(current_price_number) and current_price_number
        else math.nan
    )
    projection_rows = [
        {"Metric": "Analyst Rating", "Value": fundamentals.get("analyst_recom", "-")},
        {"Metric": "1Y Target", "Value": format_money(target_price)},
        {"Metric": "Target Upside", "Value": format_percent(upside)},
        {"Metric": "EPS Next Q", "Value": fundamentals.get("eps_next_q", "-")},
        {"Metric": "EPS Next Y", "Value": fundamentals.get("eps_next_y", "-")},
        {"Metric": "EPS Next 5Y", "Value": fundamentals.get("eps_next_5y", "-")},
        {"Metric": "Sales Next Y", "Value": fundamentals.get("sales_next_y", "-")},
        {"Metric": "Sales Q/Q", "Value": fundamentals.get("sales_qoq", "-")},
        {"Metric": "EPS Q/Q", "Value": fundamentals.get("eps_qoq", "-")},
    ]
    projection_df = pd.DataFrame(projection_rows)

    def projection_style(row: pd.Series) -> list[str]:
        if row.get("Metric") != "Target Upside":
            return ["" for _ in row.index]
        value = str(row.get("Value") or "")
        color = "#dc2626" if value.startswith("-") else "#16a34a"
        return [f"color: {color}; font-weight: 700;" if column == "Value" else "" for column in row.index]

    st.subheader("Analyst Projections")
    st.dataframe(
        projection_df.style.apply(projection_style, axis=1),
        use_container_width=True,
        hide_index=True,
    )


def render_price_chart(
    symbol: str,
    period_label: str,
    chart_type: str,
    api_key: str,
    quote_record: Optional[Dict[str, object]] = None,
    manual_earnings_event: Optional[Dict[str, object]] = None,
) -> None:
    try:
        result = fetch_chart_data(symbol, period_label, api_key)
    except Exception as exc:
        st.error(f"Could not load chart data for {symbol}: {polygon_error_message(exc)}")
        return

    chart_df = result["data"]
    meta = result["meta"]
    earnings = (
        choose_best_earnings_event([manual_earnings_event])
        if manual_earnings_event
        else fetch_earnings_data(symbol, api_key)
    )
    earnings_events, earnings_cache_result = ensure_earnings_events_for_chart(symbol, earnings, manual_earnings_event)
    earnings = selected_earnings_from_events(earnings, earnings_events)
    if not isinstance(chart_df, pd.DataFrame) or chart_df.empty:
        st.info(f"No chart data returned for {symbol}.")
        return

    snapshot = fetch_snapshot_quote(symbol, api_key)
    quote_record = quote_record or {}
    latest_price = first_valid_value(
        snapshot_current_price(snapshot),
        quote_record.get("Current Price"),
        chart_df["Close"].dropna().iloc[-1],
    )
    snapshot_prev_day = snapshot.get("prevDay") if isinstance(snapshot.get("prevDay"), dict) else {}
    previous_close = first_valid_value(meta.get("chartPreviousClose"), snapshot_prev_day.get("c"))
    latest_price_number = numeric_value(latest_price)
    previous_close_number = numeric_value(previous_close)
    fallback_change = (
        latest_price_number - previous_close_number
        if not pd.isna(latest_price_number) and not pd.isna(previous_close_number)
        else math.nan
    )
    latest_change = first_valid_value(fallback_change, quote_record.get("Change"))
    render_price_header(symbol, latest_price, latest_change, f"{period_label}")

    render_quote_stats_table(symbol, chart_df, meta, earnings, api_key, snapshot, quote_record)

    selected_earnings_events = render_earnings_event_selector(symbol, earnings_events)
    price_chart_earnings_events = visible_earnings_events(chart_df, selected_earnings_events)
    if earnings_events:
        st.caption(
            f"📅 Earnings reports loaded: {len(earnings_events)}; selected: {len(selected_earnings_events)}; "
            f"shown on price chart: {len(price_chart_earnings_events)}"
        )
    else:
        cache_error = str(earnings_cache_result.get("error") or "").strip()
        if cache_error:
            st.warning(f"No earnings date available yet. Automatic earnings lookup failed: {cache_error}")
        else:
            st.info("No earnings date found after automatic lookup.")

    chart_col, average_col = st.columns([5, 1])
    with average_col:
        selected_ma_windows = render_moving_average_controls(symbol)
        show_cross_markers = st.checkbox(
            "Show crosses",
            value=False,
            key=f"show_cross_markers_{symbol}",
            help="Toggle crossover markers on the price chart. The Patterns table stays visible.",
        )

    if chart_type == "Candlestick" and {"Open", "High", "Low", "Close"}.issubset(chart_df.columns):
        fig = go.Figure(
            data=[
                go.Candlestick(
                    x=chart_df["Datetime"],
                    open=chart_df["Open"],
                    high=chart_df["High"],
                    low=chart_df["Low"],
                    close=chart_df["Close"],
                    name=symbol,
                )
            ]
        )
        fig.update_layout(title=f"{symbol} Price Chart", xaxis_rangeslider_visible=False)
    else:
        fig = px.line(chart_df, x="Datetime", y="Close", title=f"{symbol} Close Price")
        fig.update_traces(line_width=2.4, connectgaps=True)

    visible_ma_df = visible_daily_history(symbol, chart_df, api_key)
    add_moving_average_lines(fig, visible_ma_df, selected_ma_windows)
    crossover_pairs = [(10, 20), (20, 50), (50, 200)]
    crossover_df = moving_average_crossover_rows(visible_ma_df, crossover_pairs)
    selected_ma_window_set = set(selected_ma_windows)
    selected_crossover_pairs = [
        pair for pair in crossover_pairs if pair[0] in selected_ma_window_set and pair[1] in selected_ma_window_set
    ]
    if show_cross_markers:
        add_moving_average_crossovers(fig, visible_ma_df, selected_crossover_pairs)

    for index, earnings_event in enumerate(price_chart_earnings_events):
        add_earnings_marker(fig, chart_df, earnings_event, show_legend=index == 0)
    apply_market_time_axis(fig, chart_df, skip_closed_hours=meta.get("timespan") == "minute")
    chart_key = f"price_chart_{symbol}_{period_label}_{chart_type}".lower().replace(" ", "_")
    with chart_col:
        st.plotly_chart(fig, use_container_width=True, key=chart_key)
        render_patterns_table(crossover_df)
        render_long_term_cross_chart(symbol, visible_ma_df, crossover_df)

    render_earnings_reaction_chart(symbol, earnings_events, api_key, latest_price)
    render_earnings_actuals_table(symbol)
    render_intraday_chart(symbol, api_key)

    if earnings.get("error"):
        with st.expander("Earnings data note", expanded=False):
            st.write(str(earnings["error"]))

    volume_columns = [column for column in ["Datetime", "Volume", "Open", "Close"] if column in chart_df.columns]
    volume_df = chart_df[volume_columns].dropna(subset=["Datetime", "Volume"])
    if not volume_df.empty and float(volume_df["Volume"].sum()) > 0:
        volume_df = volume_df.copy()
        volume_df["Close"] = pd.to_numeric(volume_df.get("Close"), errors="coerce")
        volume_df["Previous Close"] = volume_df["Close"].shift(1)
        if "Open" in volume_df.columns:
            volume_df["Open"] = pd.to_numeric(volume_df["Open"], errors="coerce")
            volume_df["Previous Close"] = volume_df["Previous Close"].fillna(volume_df["Open"])
        volume_df["Volume Color"] = volume_df.apply(
            lambda row: "#ef4444" if row["Close"] < row["Previous Close"] else "#22c55e",
            axis=1,
        )
        volume_fig = go.Figure(
            data=[
                go.Bar(
                    x=volume_df["Datetime"],
                    y=volume_df["Volume"],
                    name="Volume",
                    marker={"color": volume_df["Volume Color"]},
                    hovertemplate="Date=%{x}<br>Volume=%{y:,}<extra></extra>",
                )
            ]
        )
        volume_fig.update_layout(title=f"{symbol} Volume")
        add_volume_average_line(volume_fig, symbol, volume_df, api_key)
        apply_market_time_axis(volume_fig, volume_df, skip_closed_hours=meta.get("timespan") == "minute")
        volume_key = f"volume_chart_{symbol}_{period_label}".lower().replace(" ", "_")
        st.plotly_chart(volume_fig, use_container_width=True, key=volume_key)

    render_analyst_projections(symbol, latest_price)


def symbol_record(df: pd.DataFrame, symbol: str) -> Dict[str, object]:
    row = df[df["Symbol"] == symbol].head(1)
    if row.empty:
        return {}
    return row.iloc[0].to_dict()


def render_symbol_summary(df: pd.DataFrame, symbol: str) -> None:
    record = symbol_record(df, symbol)
    if not record:
        return
    detail_1, detail_2, detail_3, detail_4 = st.columns(4)
    detail_1.metric("CSV Price", format_money(record.get("Current Price")))
    detail_2.metric("CSV Change", f"{record.get('Change', '-')}")
    detail_3.metric("Day High", format_money(record.get("High")))
    detail_4.metric("Day Low", format_money(record.get("Low")))


def render_symbol_row_details(df: pd.DataFrame, symbol: str) -> None:
    record = symbol_record(df, symbol)
    if not record:
        return
    with st.expander("CSV row details", expanded=False):
        st.json({key: None if pd.isna(value) else value for key, value in record.items()})


def filter_symbols(df: pd.DataFrame, query: str) -> pd.DataFrame:
    if not query.strip():
        return df
    query_lower = query.strip().lower()
    return df[
        df.apply(
            lambda row: any(query_lower in str(value).lower() for value in row.values),
            axis=1,
        )
    ].copy()


def main() -> None:
    render_header()

    saved_polygon_api_key = configured_polygon_api_key("")
    portfolio_source_path = DEFAULT_PORTFOLIO_PATH
    selected_uploaded_file = None
    auto_refresh_prices = False
    with st.sidebar:
        st.header("Navigation")
        uploaded_file = st.file_uploader("Upload portfolio CSV", type=["csv"])
        if uploaded_file is not None:
            use_uploaded_file = st.checkbox(
                "Use uploaded portfolio CSV",
                value=False,
                help="Leave off to use the FinViz repo portfolio.csv.",
            )
            if use_uploaded_file:
                selected_uploaded_file = uploaded_file
                st.caption(f"Portfolio source: uploaded `{uploaded_file.name}`")
            else:
                st.caption("Uploaded CSV is ignored; using FinViz repo portfolio.csv.")
        else:
            st.caption(f"Portfolio source: `{portfolio_source_path}` ({format_file_age(portfolio_source_path)})")
        if st.button("Refresh local data", key="refresh_local_data"):
            st.cache_data.clear()
            st.rerun()
        auto_refresh_prices = st.checkbox(
            "Auto-refresh prices",
            value=False,
            help="Reloads the page during market hours. Keep this off to preserve your current view.",
        )
        if saved_polygon_api_key:
            st.success("Polygon.io API key loaded from local secrets.")
        else:
            st.warning("Polygon.io API key is not configured.")
        sidebar_polygon_key = st.text_input(
            "Polygon.io API key override",
            value="",
            type="password",
            placeholder="Optional override",
            help="Leave blank to use POLYGON_API_KEY from environment or .streamlit/secrets.toml.",
        )

    polygon_api_key = configured_polygon_api_key(sidebar_polygon_key) or saved_polygon_api_key
    portfolio_df = merge_added_tickers(read_portfolio(uploaded_file=selected_uploaded_file, path=portfolio_source_path))
    if portfolio_df.empty:
        st.warning("No portfolio rows found. Upload a Yahoo-style portfolio CSV or add a ticker to continue.")
        return
    portfolio_df = apply_live_ticker_prices(portfolio_df, polygon_api_key)
    if polygon_api_key and auto_refresh_prices:
        enable_live_refresh()

    with st.sidebar:
        query_page = st.query_params.get("page", "Overview")
        default_page = query_page if query_page in PAGE_OPTIONS else "Overview"
        selected_page = st.radio(
            "Page",
            PAGE_OPTIONS,
            index=PAGE_OPTIONS.index(default_page),
            horizontal=False,
            key="selected_page",
        )
        st.query_params["page"] = selected_page
        st.divider()
        symbols = portfolio_df["Symbol"].dropna().astype(str).tolist()
        query_symbol = clean_symbol(st.query_params.get("symbol", ""))
        default_symbol_index = symbols.index(query_symbol) if query_symbol in symbols else 0
        selected_symbol = st.selectbox(
            "Symbol",
            options=symbols,
            index=default_symbol_index,
            accept_new_options=True,
            placeholder="Select or type a ticker",
            help=f"Type a new ticker and press Enter to save it in {ADDED_TICKERS_PATH}.",
        )
        selected_symbol = clean_symbol(selected_symbol)
        if selected_symbol and selected_symbol not in symbols:
            if ticker_exists(selected_symbol, polygon_api_key):
                save_added_ticker(selected_symbol)
                portfolio_df = merge_added_tickers(read_portfolio(uploaded_file=selected_uploaded_file, path=portfolio_source_path))
                portfolio_df = apply_live_ticker_prices(portfolio_df, polygon_api_key)
                symbols = portfolio_df["Symbol"].dropna().astype(str).tolist()
                st.success(f"Added {selected_symbol}")
            else:
                st.warning(f"Could not find ticker `{selected_symbol}`. Not saved.")
                selected_symbol = symbols[default_symbol_index]
        if selected_symbol and selected_symbol in symbols:
            st.query_params["symbol"] = selected_symbol
        query_range = str(st.query_params.get("range", "1D"))
        range_options = list(PERIOD_OPTIONS.keys())
        default_range_index = range_options.index(query_range) if query_range in range_options else range_options.index("1D")
        period_label = st.radio("Range", range_options, index=default_range_index, horizontal=False, key="selected_range")
        st.query_params["range"] = period_label
        chart_options = ["Line", "Candlestick"]
        query_chart = str(st.query_params.get("chart", "Line"))
        default_chart_index = chart_options.index(query_chart) if query_chart in chart_options else 0
        chart_type = st.radio("Chart", chart_options, index=default_chart_index, horizontal=False, key="selected_chart")
        st.query_params["chart"] = chart_type
        st.divider()
        manual_earnings_event = None
        query_search = str(st.query_params.get("search", ""))
        search_text = st.text_input(
            "Search portfolio",
            value=query_search,
            placeholder="symbol, comment, price...",
            key="portfolio_search",
        )
        if search_text.strip():
            st.query_params["search"] = search_text.strip()
        elif "search" in st.query_params:
            del st.query_params["search"]

    filtered_df = filter_symbols(portfolio_df, search_text)

    if selected_page == "Overview":
        render_portfolio_metrics(portfolio_df)
        left_col, right_col = st.columns([2, 1])
        with left_col:
            st.subheader("Watchlist")
            render_watchlist_table(filtered_df)
        with right_col:
            st.subheader("Allocation")
            render_allocation_chart(portfolio_df, key="overview_allocation_chart")

    elif selected_page == "Charts":
        selected_record = symbol_record(portfolio_df, selected_symbol)
        render_price_chart(
            selected_symbol,
            period_label,
            chart_type,
            polygon_api_key,
            selected_record,
            manual_earnings_event,
        )
        render_symbol_row_details(portfolio_df, selected_symbol)

    elif selected_page == "Patterns":
        render_all_patterns_page(symbols, polygon_api_key)

    elif selected_page == "Day Trade":
        render_day_trade_page(symbols, polygon_api_key)

    elif selected_page == "Compare":
        render_raju_padmaja_compare_page(polygon_api_key)

    elif selected_page == "Holdings":
        st.subheader("Holdings And Gain/Loss")
        holdings_df = portfolio_df[portfolio_df["Quantity"].fillna(0) > 0].copy()
        if holdings_df.empty:
            st.info("Your CSV currently has Quantity values only for rows that are holdings. Add Quantity and Purchase Price to track gain/loss.")
        else:
            render_watchlist_table(holdings_df.sort_values("Market Value", ascending=False))
            render_allocation_chart(holdings_df, key="holdings_allocation_chart")

    elif selected_page == "Research Notes":
        st.subheader("Research Notes")
        st.write(
            "Use the `Comment`, `High Limit`, and `Low Limit` columns in your CSV as lightweight watchlist notes. "
            "This dashboard will display those fields automatically."
        )
        notes_columns = [column for column in ["Symbol", "High Limit", "Low Limit", "Comment"] if column in portfolio_df.columns]
        st.dataframe(portfolio_df[notes_columns], use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
