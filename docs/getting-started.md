# Getting Started

This guide takes you from a fresh clone to a running platform, then through a first session with each interface. Allow about ten minutes, most of it compiling.

## Prerequisites

| Tool            | Version              | Needed for                                                       |
| :-------------- | :------------------- | :--------------------------------------------------------------- |
| C++ compiler    | GCC 13+ or Clang 16+ | The engine uses C++20 features including `<format>` and `<span>` |
| CMake           | 3.16+                | Configuring and building the backend                             |
| Node.js and npm | 18+ (CI uses 20)     | Building the web terminal                                        |
| Python and pip  | 3.9+ for data fetch  | `scripts/fetch_data.py`                                          |
| Python          | 3.11+ for the AI     | `ai_models` and `ai_services` (CI tests 3.11 and 3.12)           |

The backend was built, and its 22 unit tests run, with GCC 13.3 while writing these documents. On an older compiler the usual failure is a missing standard library header such as `<format>`, not a syntax error.

## 1. Get the code

```bash
git clone https://github.com/quantsingularity/AlphaForge.git
cd AlphaForge
```

## 2. Get market data

The repository ships no market data. Every component reads the same directory of CSV files, `code/backend/data`, so this step is shared by the engine, the web terminal and the AI models.

```bash
pip install -r requirements.txt
python scripts/fetch_data.py --period 3y
```

This downloads split and dividend adjusted daily prices from Yahoo Finance for AAPL, MSFT, SPY, NVDA, JPM and TLT, and writes one `SYMBOL.csv` per ticker. Use `--tickers` to choose others and `--start` and `--end` for an explicit range. The full option list is in [Data](data.md#fetching-data).

If you cannot reach Yahoo Finance, you have two options:

- Put your own CSV files in `code/backend/data` using the format in [Data](data.md#csv-format).
- Generate synthetic series for exploring the software. This is only for trying things out, and the results mean nothing about markets:

```bash
PYTHONPATH=code python - <<'PY'
from pathlib import Path
from ai_models.testing import synthetic_bars, write_csv

out = Path("code/backend/data")
out.mkdir(parents=True, exist_ok=True)
for seed, symbol in enumerate(["AAA", "BBB", "CCC"], start=1):
    write_csv(synthetic_bars(n=800, seed=seed, start="2021-01-04"), out / f"{symbol}.csv")
PY
```

This needs `numpy` and `pandas`, which come with `pip install -r code/ai_models/requirements.txt`.

## 3. Build and test the backend

```bash
cd code/backend
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j
cd build && ctest --output-on-failure
```

The build takes about a minute on a single core and produces four programs in `code/backend/build`:

| Binary              | Purpose                                         |
| :------------------ | :---------------------------------------------- |
| `alphaforge_server` | REST API, accounts, and static host for the web |
| `alphaforge_cli`    | Interactive terminal                            |
| `alphaforge_tests`  | The 22 unit tests                               |
| `alphaforge_bench`  | Micro benchmark harness                         |

A healthy run ends with `22 passed, 0 failed, 22 total` when you run `./alphaforge_tests`, and `100% tests passed` from CTest.

The shortcut `scripts/build_and_test.sh` does the build and the tests in one step.

## 4. Build the web terminal

```bash
cd frontend
npm install
npm run build
```

This type-checks the project and writes the production bundle to `frontend/dist`.

## 5. Run everything

The simplest way is the helper script. It fetches data if there is none, builds the backend and the web terminal (incrementally, so repeat runs are quick), and starts the server:

```bash
scripts/run_stack.sh
```

Or start the server yourself:

```bash
code/backend/build/alphaforge_server 8080 code/backend/data frontend/dist
```

The three arguments are the port, the data directory and the directory of the built web terminal. All are optional. See [Command line programs](cli.md#the-rest-server) for the defaults and the details.

Open `http://localhost:8080`. To check the API directly:

```bash
curl http://localhost:8080/api/health
```

```json
{ "status": "ok", "symbols_loaded": 6, "threads": 8, "version": "1.0.0" }
```

`symbols_loaded` is the number of symbols the server loaded data files for. If it is `0`, the server started but found no data, which is the most common first-run problem. See [If something goes wrong](#10-if-something-goes-wrong).

## 6. First session in the web terminal

1. Choose **Sign up** and create an account. Any name of two or more characters, a valid-looking email and a password of at least eight characters will do. Accounts are stored in `code/backend/data/users.json`.
2. The terminal opens on **Market Data**. Pick an instrument to see its closing price chart and recent bars.
3. Open **Strategy Lab**, choose an instrument and a strategy, and run a backtest. The result compares the strategy with buy and hold over the same window.
4. Open **Risk** for a cross-sectional table of every instrument, computed in parallel, and a single-instrument detail view with an optional beta benchmark.
5. Open **Order Ticket**, place a small market order, then look at **Portfolio** for the position, allocation and trade blotter. The order ticket has an **Undo Last** button that reverses the most recent trade.

The panels are described in [Web terminal](frontend.md).

## 7. First session in the CLI

The CLI reads market data from a directory named `data` relative to the directory you start it in, and takes no arguments. From the backend directory that is the right place:

```bash
cd code/backend
./build/alphaforge_cli
```

Choose `2` to view a symbol, `5` to run a backtest, `6` for risk. Option `1` reloads from another directory. See [CLI](cli.md).

## 8. The AI components

The AI side is optional and independent of the C++ build.

```bash
pip install -r code/ai_services/requirements.txt
```

Train models for every symbol in the data directory and ask for a prediction:

```bash
export PYTHONPATH=code
python -m ai_models train
python -m ai_models predict --symbol AAPL
```

Or run the HTTP service, which trains any missing models on startup, and query it:

```bash
python -m ai_services
curl http://127.0.0.1:8090/ai/signals
```

Training writes to `code/ai_models/artifacts`. The first start can take a while, because the service does not answer requests until startup training has finished. The service documents itself: open `http://127.0.0.1:8090/ai/docs` for every endpoint and its parameters. The source is in `code/ai_models` and `code/ai_services`.

## 9. Where to go next

| Goal                         | Document                                    |
| :--------------------------- | :------------------------------------------ |
| Understand what you just ran | [Architecture](architecture.md)             |
| Script against the API       | [REST API reference](rest-api.md)           |
| Learn what each metric means | [Risk and analytics](risk-and-analytics.md) |
| Understand a backtest result | [Backtesting](backtesting.md)               |
| Learn the data format        | [Data](data.md)                             |
| Use the terminal programs    | [Command line programs](cli.md)             |

## 10. If something goes wrong

| Symptom                                                              | Cause and fix                                                                                                                                                                                                                                                |
| :------------------------------------------------------------------- | :----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `symbols_loaded` is `0`, or the web terminal shows no instruments    | The data directory is empty or the path is wrong. The server's data path is relative to where you start it. Fetch data, then start the server with an explicit path, for example `code/backend/build/alphaforge_server 8080 code/backend/data frontend/dist` |
| The CLI says `loaded 0 symbols` and `data directory not found: data` | The CLI looks for `data` in the current directory. Start it from `code/backend`, or choose menu option 1 and enter the path                                                                                                                                  |
| The server logs `listening` and then `failed to bind port`           | The port is already in use. The `listening` line is printed before the bind attempt, so trust the second line. Pick another port as the first argument                                                                                                       |
| The server starts on 8080 whatever port you pass                     | A port argument that is not a number is silently replaced by 8080                                                                                                                                                                                            |
| A symbol loads with fewer bars than the file has rows                | Rows failed validation. The server log shows `loaded SYMBOL: N bars, R rejected`. See [Data](data.md#validation-rules)                                                                                                                                       |
| `fetch_data.py` fails for every ticker                               | Yahoo Finance is unreachable from your network. Allow `query1.finance.yahoo.com` and `query2.finance.yahoo.com`, or supply your own CSV files                                                                                                                |
| The CLI prints `Invalid option.` endlessly                           | Its input closed without an exit command. Always end scripted input with `11` or `q`. See [Command line programs](cli.md#scripting-the-terminal)                                                                                                             |
| You are signed out after restarting the server                       | Sessions live in memory. Sign in again; accounts are saved on disk                                                                                                                                                                                           |
| The AI service does not answer right after starting                  | It trains missing models before it accepts requests. Wait for startup training to finish                                                                                                                                                                     |
