# Command Line Programs

The backend build produces four programs. This document covers how to run each, with the exact prompts and behaviour of the interactive terminal.

| Program             | Purpose                           | Takes arguments                     |
| :------------------ | :-------------------------------- | :---------------------------------- |
| `alphaforge_cli`    | Interactive, menu-driven terminal | None                                |
| `alphaforge_server` | REST API, accounts and web host   | Port, data directory, web directory |
| `alphaforge_bench`  | Micro benchmark report            | None                                |
| `alphaforge_tests`  | The 22 unit tests                 | None                                |

All four are built into `code/backend/build` by the commands in [Getting started](getting-started.md#3-build-and-test-the-backend).

## The interactive terminal

```bash
cd code/backend
./build/alphaforge_cli
```

The program reads price data from a directory named `data` **relative to the directory you start it in**, and takes no arguments. Starting it from `code/backend` finds `code/backend/data`. Starting it from anywhere else loads nothing and prints a warning:

```
[WARN ] data directory not found: data
AlphaForge loaded 0 symbols from 'data'.
```

If that happens, either start it from the right directory or choose option 1 and type the correct path.

A normal start prints the symbol count and the menu:

```
AlphaForge loaded 2 symbols from 'data'.
================================================================
  AlphaForge Quant Terminal
================================================================
  1.  Load Market Data
  2.  View Market Data
  3.  Portfolio Dashboard
  4.  Submit Order
  5.  Run Backtest
  6.  Risk Analysis
  7.  Analytics Dashboard
  8.  Performance Benchmark Report
  9.  Watchlist
  10. Undo Last Trade
  11. Exit

Select an option:
```

Type a number and press Enter. `11` or `q` exits. Anything else prints `Invalid option.` and shows the menu again.

### Menu reference

| Option | Prompts                                    | What it does                                                                                                |
| :----- | :----------------------------------------- | :---------------------------------------------------------------------------------------------------------- |
| 1      | `Data directory [data]:`                   | Reloads market data. Press Enter to keep the current directory. Prints `Loaded N bars across M symbols.`    |
| 2      | `Symbol:`                                  | Prints the last 10 bars as a table of date, open, high, low, close and volume                               |
| 3      | none                                       | Cash, holdings value, total value, realized and unrealized P&L, gross and net exposure, then open positions |
| 4      | `Symbol:`, `Side (buy/sell):`, `Quantity:` | Submits a **market** order and fills it at the latest close                                                 |
| 5      | `Symbol:`, then `Strategy:`                | Runs a backtest and prints the metrics and a buy-and-hold comparison                                        |
| 6      | `Symbol:`                                  | Annualized volatility, Sharpe, Sortino, max drawdown, VaR and CVaR at 95%                                   |
| 7      | `Symbol:`                                  | Annualized return and the six most recent monthly returns                                                   |
| 8      | none                                       | Times the engine on the first loaded symbol. A quick performance check                                      |
| 9      | `> `                                       | Watchlist: `add SYM`, `remove SYM` or `show`                                                                |
| 10     | none                                       | Reverses the most recent trade and prints the resulting cash                                                |
| 11     | none                                       | Exit                                                                                                        |

An unknown symbol prints `Unknown symbol.` and returns to the menu.

### A session

This transcript was produced against synthetic data. Menu text is omitted for brevity.

```
Select an option: 2
Symbol: AAA

Last 10 bars for AAA:
Date              Open      High       Low     Close        Volume
2024-04-08      145.74    148.20    144.63    147.08        958581
...
2024-04-19      144.55    148.36    142.79    146.58        891252

Select an option: 4
Symbol: AAA
Side (buy/sell): buy
Quantity: 10
Order 1 filled at 146.58

Select an option: 3
Cash:            998534.20
Holdings value:  1465.80
Total value:     1000000.00
...
Positions:
  AAA      qty      10.00 @     146.58  uPnL       0.00

Select an option: 5
Symbol: AAA
Strategies: BuyAndHold MovingAverageCrossover Momentum MeanReversion
Strategy: sma

  Backtest: MovingAverageCrossover on AAA
Total return:    16.8397 %
CAGR:            6.7666 %
Volatility:      16.6797 %
Sharpe:          0.3559
Sortino:         0.4238
Max drawdown:    15.0308 %
Win rate:        51.1688 %
Profit factor:   1.1013
Trades:          14
Benchmark (buy and hold) CAGR: 17.3880 %, Sharpe: 0.7791
```

The backtest accepts the same names and aliases as the REST API (`sma`, `mom`, `mr`, `bh` and so on), in any letter case. See [Backtesting](backtesting.md#choosing-a-strategy-by-name). An unknown name prints `Unknown strategy.`

The numbers are identical to what the REST API returns for the same request, because both call the same engine. The CLI prints percentages to four decimals.

### Things that are different in the CLI

- **Only market orders.** Option 4 has no limit or stop order entry.
- **`sell` must be exactly lower case.** Any other text, including `SELL`, `Sell` and typos, places a **buy**. The REST API is slightly more lenient (it also accepts `SELL`), but both default silently to a buy. This was verified: entering `SELL` opened a long position.
- **Quantity must parse as a number.** Otherwise `Invalid quantity.` is printed and nothing happens.
- **The watchlist is not persistent.** It lives in the running process and is gone when you exit. `show` is also what any unrecognised watchlist command does.
- **State is not shared.** The CLI has its own portfolio and market data, separate from a running server. Trading in one does not show in the other, and neither saves its portfolio.
- **Logging is quieter.** The CLI logs warnings and errors only, to keep the menu readable. The server logs at info level.

### Scripting the terminal

You can drive the terminal from a pipe. Two rules matter:

1. **Always end the input with `11` or `q`.**
2. Send exactly the lines the prompts expect, in order.

```bash
cd code/backend
printf '2\nAAA\n6\nAAA\n11\n' | ./build/alphaforge_cli
```

**Warning:** If standard input closes before you exit, the program does not stop. It reads an empty line forever and prints `Invalid option.` in a tight loop, producing over a gigabyte of output in two seconds. This happens with `./build/alphaforge_cli < /dev/null`, or with piped input that does not end with `11` or `q`. If you script it, always send the exit command, and consider wrapping the call in `timeout`. For anything beyond a quick check, use the REST API, which is built for programmatic access.

## The REST server

```bash
code/backend/build/alphaforge_server [port] [data_dir] [web_dir]
```

| Position | Default                             | Meaning                                                  |
| :------- | :---------------------------------- | :------------------------------------------------------- |
| 1        | `8080`, or `ALPHAFORGE_PORT` if set | Port to listen on                                        |
| 2        | `data`                              | Directory of price CSVs, also where `users.json` is kept |
| 3        | `frontend`                          | Directory of the built web terminal                      |

Paths are relative to the working directory, so the defaults work when you start the server from the directory that contains `data` and `frontend`. The repository's helper script uses absolute paths.

Behaviour worth knowing:

- **`ALPHAFORGE_PORT` applies only when there are no arguments at all.** If you pass any argument, the environment variable is ignored.
- **A port that is not a number is silently replaced with 8080.** `alphaforge_server abc` listens on 8080. There is no error.
- **The server listens on all interfaces** (`0.0.0.0`), not just localhost.
- **The "listening" log line comes before the bind attempt.** If the port is already taken, you see `AlphaForge API listening on ...` followed by `failed to bind port N`, and the process exits with status 1. Trust the second line.
- **A missing web directory is not an error.** The API works, the static files are not served, and the log omits the `serving frontend from` line.
- **Stop it with Ctrl+C.** Accounts are saved to `users.json` when they are created, so there is nothing to flush on exit, but the portfolio and sessions are lost.

Typical startup log:

```
[INFO ] loaded AAA: 600 bars, 0 rejected
[INFO ] loaded BBB: 600 bars, 0 rejected
[INFO ] bootstrap loaded 1200 bars
[INFO ] serving frontend from frontend/dist
[INFO ] AlphaForge API listening on http://0.0.0.0:8080
```

If a symbol shows a nonzero `rejected` count, rows failed validation. See [Data](data.md#validation-rules).

## The benchmark program

```bash
cd code/backend
./build/alphaforge_bench
```

Like the CLI, it reads `data` relative to the working directory. Without data it prints `No data found in 'data'. Generate it first.` and exits.

It times five operations and prints one line each:

| Line                                  | What is measured                                               |
| :------------------------------------ | :------------------------------------------------------------- |
| Load market data                      | Parsing and validating every CSV                               |
| Close series x100                     | Extracting a symbol's closing prices 100 times                 |
| Simple returns x100                   | Computing returns from the closes 100 times                    |
| Backtest x50 (MA crossover)           | Fifty backtests of the moving average strategy                 |
| Risk metrics, sequential and parallel | The batch of risk metrics, one thread and then the thread pool |

Sample output on a single-core machine with two synthetic symbols:

```
Symbols: 2, threads: 1

Load market data (2 symbols)                   2.193 ms
Close series x100 (AAA)                        0.020 ms
Simple returns x100                            0.148 ms
Backtest x50 (MA crossover)                    4.818 ms
Risk metrics sequential                        0.095 ms
Risk metrics parallel batch                    0.129 ms   (single core machine: expect no speedup)
```

The parallel line only beats the sequential one when the machine has more than one hardware thread and enough symbols to split. The timings depend on your hardware and data size. Use them to compare before and after a change on the same machine, not as absolute figures.

Option 8 of the interactive terminal runs a shorter version of the same idea.

## The unit tests

```bash
cd code/backend/build
./alphaforge_tests        # runs all 22 tests, exits non-zero on any failure
ctest --output-on-failure # the same suite through CTest
```

## Helper scripts

| Script                      | What it does                                                                          |
| :-------------------------- | :------------------------------------------------------------------------------------ |
| `scripts/fetch_data.py`     | Downloads data. See [Data](data.md#fetching-data)                                     |
| `scripts/build_and_test.sh` | Configures, builds and runs the C++ tests in one step                                 |
| `scripts/run_stack.sh`      | Fetches data if there is none, builds the backend and web terminal, starts the server |

`run_stack.sh` does the following, in order, and stops at the first failure:

1. If `code/backend/data` has no CSV files, it installs `requirements.txt` and runs the fetch script for three years of data. If the fetch fails it exits with advice instead of starting.
2. Configures and builds the backend into `code/backend/build`. This is incremental, so it is fast when nothing changed.
3. Runs `npm install` only if `frontend/node_modules` is missing, then always runs `npm run build`.
4. Starts the server with absolute paths to the data directory and `frontend/dist`.

The port comes from the `ALPHAFORGE_PORT` environment variable and defaults to 8080. The script takes no arguments:

```bash
ALPHAFORGE_PORT=9000 scripts/run_stack.sh
```
