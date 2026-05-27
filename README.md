# Gan Portfolio Dashboard

Personal stock dashboard for the ticker CSV in this repo:

```text
/Users/rajuaddala/ecp_code/FinViz/portfolio.csv
```

## Features

- Yahoo Finance-style symbol navigation.
- Watchlist table from your CSV.
- Portfolio metrics for rows with `Quantity`.
- Allocation pie chart when quantities are present.
- Line and candlestick charts using Polygon.io aggregate bars.
- Volume chart.
- Moving averages: 10-day, 20-day, 50-day, and 200-day overlays.
- Earnings date metric and chart marker from manual sidebar entry, `earnings.csv`, or Polygon earnings data when available.
- Research notes from `Comment`, `High Limit`, and `Low Limit`.

This is for personal tracking only and is not investment advice.

## Run

Set your Polygon.io API key first:

```bash
export POLYGON_API_KEY="your_polygon_api_key"
```

You can also paste the key into the dashboard sidebar at runtime.

```bash
cd /Users/rajuaddala/Documents/GanRoot/Ganesh_home/Gan_investments/gan-ui
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

If you use `uv`:

```bash
uv run --with streamlit --with pandas --with plotly --with requests streamlit run app.py
```

## Polygon.io Data

Charts use Polygon.io stock aggregate bars:

- intraday ranges use minute bars
- longer ranges use day, week, or month bars
- previous close is fetched separately for change calculations

Some symbols in a Yahoo export may not exist as Polygon stock tickers, especially index symbols like `^IXIC` or mutual funds. In that case, the chart tab will show a clear API error while the CSV watchlist still works.

Earnings data first checks local manual inputs, then Polygon Benzinga Earnings. If your Polygon plan does not include that add-on, the dashboard will still show price charts and moving averages.

The earnings lookup order is:

1. `earnings.csv`
2. `data/earnings_history.csv`
3. Polygon Benzinga Earnings, if your Polygon plan includes it

To scrape and locally cache the past two years of earnings calendar data for only the stocks in your portfolio:

```bash
./scripts/fetch_earnings_history.py
```

The script reads `portfolio.csv` from this repo, checks `data/earnings_history.csv` first, and only scrapes symbols that are not already saved. All tickers are appended into the same local file. Re-running it is safe.

When you add a new stock to your portfolio CSV, run:

```bash
./scripts/fetch_earnings_history.py
```

Only the new/missing ticker gets scraped. To fetch one ticker manually:

```bash
./scripts/fetch_earnings_history.py --symbols AAPL
```

To force a full portfolio re-fetch:

```bash
./scripts/fetch_earnings_history.py --force
```

If a portfolio symbol has no earnings calendar rows, the script writes a `NO_EARNINGS_FOUND` cache row for that symbol so it does not keep scraping the same ticker again.

To show earnings dates without the Polygon Benzinga add-on, use either:

- Sidebar quick entry: select a symbol, check `Set earnings date for chart`, and choose a date.
- Persistent CSV: edit `earnings.csv` in this folder.

`earnings.csv` format:

```csv
Symbol,Earnings Date,Time,Source
ORCL,2026-06-10,AMC,Manual
AAPL,2026-07-30,AMC,Manual
```

Supported time values are free text; common values are `BMO` before market open and `AMC` after market close. If an earnings date is outside the selected chart range, the chart shows an orange note. If it is near the visible chart window, the chart expands enough to display the earnings marker.

## Morningstar Account Data

Use Morningstar as an import source by exporting your portfolio/holdings to CSV from your Morningstar account, then upload that CSV in the dashboard sidebar. Do not put your Morningstar username or password into this app.

The dashboard now recognizes common Morningstar-style columns:

- `Ticker`, `Ticker Symbol`, or `Holding Ticker` as `Symbol`
- `Shares` or `Units` as `Quantity`
- `Price`, `Market Price`, or `Last Price` as `Current Price`
- `Value` or `Current Value` as `Market Value`
- `Average Cost`, `Avg Cost`, or `Cost Per Share` as `Purchase Price`
- `Notes` as `Comment`

Polygon.io still powers the live charts and moving averages. Morningstar CSV data powers your watchlist, holdings, cost basis, and notes.

## CSV Format

The dashboard expects a Yahoo-style or Morningstar-style CSV with at least:

- `Symbol`

`Current Price` and `Change` are optional if you only want symbol navigation and Polygon charts.

Optional columns unlock more features:

- `Purchase Price`
- `Quantity`
- `Comment`
- `High Limit`
- `Low Limit`

## Script Workflow

Default port: `8181`


Initialize the local virtual environment:

```bash
./scripts/init.sh
```

Start the dashboard:

```bash
export POLYGON_API_KEY="your_polygon_api_key"
./scripts/start.sh
```

Restart the dashboard:

```bash
./scripts/restart.sh
```

Stop the dashboard:

```bash
./scripts/stop.sh
```

Optional port override:

```bash
PORT=8182 ./scripts/restart.sh
```
