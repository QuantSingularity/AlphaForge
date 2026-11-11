# Data

Every component of AlphaForge reads prices from one place: a directory of CSV files, one per instrument. This document defines the format, explains how strictly each component validates it, and covers fetching and refreshing data.

The repository ships no market data. Results always come from files you fetch or provide.

## CSV format

```
date,open,high,low,close,volume
2022-01-03,177.83,182.88,177.71,182.01,104487900
2022-01-04,182.63,182.94,179.12,179.7,99310400
```

| Column   | Type    | Meaning                                    |
| :------- | :------ | :----------------------------------------- |
| `date`   | text    | Trading day, ISO 8601 `YYYY-MM-DD`         |
| `open`   | decimal | Opening price                              |
| `high`   | decimal | Highest price of the day                   |
| `low`    | decimal | Lowest price of the day                    |
| `close`  | decimal | Closing price                              |
| `volume` | integer | Shares traded (write it without a decimal) |

Conventions that keep a file loadable by every component:

- Name the file `SYMBOL.csv`. The file name without the extension is the symbol.
- Include the header row exactly as above.
- Use ISO dates.
- Write volume as a whole number.
- Use adjusted prices (see [Adjusted prices](#adjusted-prices)).

### Symbols

The symbol is the file name without `.csv`.

| Component   | Accepted symbols                                                                         | Case                                               |
| :---------- | :--------------------------------------------------------------------------------------- | :------------------------------------------------- |
| C++ engine  | Any file stem. REST routes accept letters, digits, `_`, `.`, `-`                         | Case-sensitive: `aapl` and `AAPL` are different    |
| `ai_models` | 1 to 16 characters, starting with a letter or digit, then letters, digits, `.`, `_`, `-` | Upper-cased internally; lookup is case-insensitive |

Name files in upper case and the two never disagree.

## Validation rules

The C++ engine and the Python code validate independently, and they differ. The differences matter if you hand-edit files.

| Condition                           | C++ engine                            | Python (`ai_models`)                           |
| :---------------------------------- | :------------------------------------ | :--------------------------------------------- |
| Missing header row                  | Accepted, first row is data           | Rejected: `missing columns: date`              |
| Header capitalised (`Date,Open`)    | Accepted (header skipped by position) | Accepted (names are lower-cased)               |
| Fewer than six columns in a row     | Row rejected                          | File rejected                                  |
| Non-numeric price                   | Row rejected                          | File rejected                                  |
| Non-integer volume (`100.0`, `1e6`) | Row rejected                          | Accepted                                       |
| Price of zero or below              | Row rejected                          | File rejected                                  |
| `low` above `high`                  | Row rejected                          | File rejected                                  |
| `open` or `close` outside the range | Row rejected                          | Reported by `validate_bars`, tolerated on load |
| Negative volume                     | Row rejected                          | File rejected                                  |
| Duplicate dates                     | Both rows kept                        | File rejected                                  |
| Rows out of date order              | Sorted on load                        | Sorted on load                                 |
| Date not in ISO form                | Accepted as a string, then mis-sorted | File rejected                                  |

Two consequences follow:

1. **Granularity.** The C++ engine never rejects a whole file for bad rows. It drops them and logs the count, for example `loaded DUP: 3 bars, 5 rejected`. Python rejects the whole file on the first hard problem. A file with one bad row therefore works in the engine, minus that row, and fails in the AI models until it is fixed.
2. **Dates are not validated by the engine.** It treats dates as strings and sorts them lexicographically. ISO dates sort correctly. A file with `01/02/2024` style dates loads without a warning and is ordered nonsensically.

**Note:** The engine keeps at most 25 row-level error messages per file in its parse report, and the server logs only the accepted and rejected counts. If a symbol loads with fewer bars than you expect, compare the count in the log line with `wc -l`.

### What the C++ engine checks per row

A bar is valid when all of these hold:

- The date is not empty.
- Open, high, low and close are all greater than zero.
- `low <= high`.
- `open` and `close` are each between `low` and `high`, inclusive. There is no tolerance.
- Volume is zero or more.

### What Python checks

`validate_bars` returns every problem found. `read_bars` raises `DataError` for the structural and numeric ones (missing columns, no rows, unsorted or duplicate dates, non-finite values, non-positive prices, negative volume, `high` below `low`). It never raises for `open` or `close` outside the high-low range (`validate_bars` reports it only when the violation exceeds one part in a million), because adjusted vendor data can violate it through rounding.

## Fetching data

`scripts/fetch_data.py` downloads daily prices from Yahoo Finance with `yfinance` and writes files in the format above.

```bash
pip install -r requirements.txt
python scripts/fetch_data.py                                  # defaults
python scripts/fetch_data.py --period 5y --tickers AAPL MSFT SPY
python scripts/fetch_data.py --start 2018-01-01 --end 2024-01-01
```

| Flag                | Default                    | Meaning                                                             |
| :------------------ | :------------------------- | :------------------------------------------------------------------ |
| `--tickers`         | AAPL MSFT SPY NVDA JPM TLT | Symbols to download                                                 |
| `--period`          | `3y`                       | Lookback when no start date is given (`1y`, `5y`, `max`, and so on) |
| `--start` / `--end` | none                       | Explicit range `YYYY-MM-DD`; a start date overrides `--period`      |
| `--out`             | `code/backend/data`        | Output directory, created if missing                                |

What the script does:

- Requests daily bars with `auto_adjust=True`, so prices are split and dividend adjusted.
- Writes prices rounded to two decimals and volume as an integer. A missing volume is written as `0`.
- Skips any row that has a non-finite price.
- Prints one line per ticker, then `Done: N symbols written, M failed.`

Exit codes: `0` when at least one symbol was written (even if others failed), `1` when nothing was written, `2` when `yfinance` is not installed.

The download needs access to `query1.finance.yahoo.com` and `query2.finance.yahoo.com`. If every ticker fails with a connection error, those hosts are blocked from your network.

### Adjusted prices

Returns, volatility and backtests are computed from the close column. Using split and dividend adjusted closes keeps the return series continuous across corporate actions. An unadjusted close shows a split as a one-day crash. `fetch_data.py` adjusts by default. If you supply your own files, use adjusted closes.

## Bringing your own data

Any source works if you write the format above. For example, converting a generic vendor file with pandas:

```python
import pandas as pd

raw = pd.read_csv("vendor.csv", parse_dates=["Date"])
out = pd.DataFrame(
    {
        "date": raw["Date"].dt.strftime("%Y-%m-%d"),
        "open": raw["Open"],
        "high": raw["High"],
        "low": raw["Low"],
        "close": raw["Adj Close"],
        "volume": raw["Volume"].round().astype("int64"),
    }
)
out.to_csv("code/backend/data/XYZ.csv", index=False)
```

Note that this example uses the adjusted close for `close` but leaves `open`, `high` and `low` unadjusted, which can leave `close` outside the high-low range after a large adjustment. If you have an adjustment factor, apply it to all four prices so each bar stays internally consistent. The C++ engine drops a bar that violates the range, so check the `rejected` count in the server log.

## Where each component reads from

| Component                 | Data directory                                                                 |
| :------------------------ | :----------------------------------------------------------------------------- |
| REST server               | Second command line argument, default `data` relative to the working directory |
| CLI and benchmark         | `data` relative to the working directory; the CLI's option 1 can change it     |
| `ai_models` CLI           | `--data-dir`, else `ALPHAFORGE_DATA_DIR`, else `code/backend/data`             |
| `ai_services`             | `--data-dir`, else `ALPHAFORGE_DATA_DIR`, else `code/backend/data`             |
| `scripts/run_stack.sh`    | `code/backend/data`                                                            |
| Docker image (engine)     | `/app/data`, populated at build time by the fetch script                       |
| Docker image (AI service) | `/data`, mounted at run time                                                   |

The server also keeps `users.json` in its data directory. The loaders look only at `.csv` files, so it does not interfere. See the [security notes](rest-api.md#security-notes) for what that file contains.

## Refreshing data

Market data is read once at startup.

| To refresh      | Do this                                                                                           |
| :-------------- | :------------------------------------------------------------------------------------------------ |
| The REST server | Run the fetch script, then restart the server                                                     |
| The CLI         | Run the fetch script, then choose option 1 (Load Market Data)                                     |
| The AI models   | Run the fetch script, then `python -m ai_models train`                                            |
| The AI service  | Run the fetch script and restart it. With auto-train on, it retrains any model whose data changed |

The AI side detects change by content, not by date. Each stored model records a fingerprint of the exact data it was trained on, a hash of every date and value. The service's `GET /ai/models` endpoint reports `stale: true` for a model whose data on disk no longer matches.

## Generating synthetic data

For trying the software offline, or for tests, `ai_models.testing` generates deterministic series with volatility clustering, gaps and volume:

```python
from pathlib import Path
from ai_models.testing import synthetic_bars, write_csv

bars = synthetic_bars(n=800, seed=1, start="2021-01-04")
write_csv(bars, Path("code/backend/data") / "AAA.csv")
```

`synthetic_bars` takes `n`, `seed`, `start_price`, `daily_drift`, `base_volatility` and `start` (the first business day). The same seed always gives the same series. The prices follow a GARCH-like recursion with a small positive drift, so they look like a market but contain no information about one. Do not draw conclusions about strategies from them.
