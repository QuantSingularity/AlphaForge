# Architecture

AlphaForge has two halves that share a directory of data and nothing else: a C++ trading and research platform, and a Python machine learning subsystem. This document explains how each is built, how requests and data move through them, and which state lives where.

## System overview

```
                         +------------------------------+
                         |  Web terminal                |
                         |  React, TypeScript, Recharts |
                         +--------------+---------------+
                                        | fetch /api/*  (JSON over HTTP)
                                        v
   +----------------------+   +------------------------------+
   |  CLI terminal        |   |  REST server                 |
   |  (stdin and stdout)  |   |  cpp-httplib, nlohmann/json  |
   +----------+-----------+   |  accounts, static web host   |
              |               +--------------+---------------+
              | direct calls                 | direct calls
              v                              v
   +------------------------------------------------------------+
   |             Engine library (C++20, one static lib)         |
   |                                                            |
   |  MarketData  Portfolio  Execution  Risk  Analytics         |
   |  Backtesting  Auth  Config  Utilities                      |
   +----------------------------+-------------------------------+
                                | reads
                                v
                      +--------------------+        reads        +----------------------+
                      |  code/backend/data | <------------------- |  ai_models           |
                      |  SYMBOL.csv files  |                      |  (Python library+CLI)|
                      +--------------------+                      +-----------+----------+
                                                                              | used by
                                                                              v
                                                                  +----------------------+
                                                                  |  ai_services         |
                                                                  |  FastAPI on :8090    |
                                                                  +----------------------+
```

Three properties of this layout matter:

1. **One engine, several front ends.** The CLI, the REST server, the unit tests and the benchmark harness all link the same static library, so there is exactly one implementation of every calculation. The CLI and the REST API return identical numbers for the same request, which is checked in [Backtesting](backtesting.md#one-implementation-two-front-ends).
2. **The AI service is a separate process.** Neither the web terminal nor the C++ server calls it. It reads the same CSV files and answers on its own port. Wiring the two together is a natural extension, but it does not exist today.
3. **The shared surface is the data directory.** Both halves treat `code/backend/data` as the source of truth for prices. See [Data](data.md).

## Repository map

```
AlphaForge/
  code/
    backend/                 C++20 engine, CLI, REST server, tests
      include/               public headers, one directory per module
      src/                   engine implementation (compiled to a static library)
      app/                   entry points: cli_main.cpp, server_main.cpp, JsonSupport.hpp
      tests/                 22 unit tests on a zero-dependency test framework
      benchmarks/            micro benchmark harness
      data/                  market data CSVs (fetched, not committed) and users.json
      third_party/           vendored cpp-httplib and nlohmann/json
    ai_models/               Python package: features, models, evaluation, registry, CLI
    ai_services/             Python package: FastAPI service over ai_models
  frontend/                  React, TypeScript, Vite web terminal
  scripts/                   build, run, fetch, smoke-test and validation scripts
  infrastructure/            Dockerfile and docker-compose.yml
  docs/                      this documentation
  .github/workflows/         the CI/CD pipeline
```

## The C++ engine

The engine compiles once into the static library `alphaforge_engine`. Every executable links it.

| Module        | Headers in `include/`                                                     | Responsibility                                                                |
| :------------ | :------------------------------------------------------------------------ | :---------------------------------------------------------------------------- |
| `common`      | `Types`, `Concepts`                                                       | Domain aliases, enums, the `AlphaForgeError` type, the floating point concept |
| `market`      | `Bar`, `MarketDataEngine`, `IMarketDataRepository`, `IMarketDataObserver` | Load and validate CSVs, store sorted series, answer queries, notify observers |
| `portfolio`   | `Position`, `Portfolio`                                                   | Average-cost accounting, marks, exposure, weights, replay-based undo          |
| `execution`   | `Order`, `OrderManager`                                                   | Order lifecycle, the marketable fill model, trade history                     |
| `risk`        | `RiskEngine`                                                              | Volatility, Sharpe, Sortino, drawdown, VaR, CVaR, beta, parallel batch        |
| `analytics`   | `AnalyticsEngine`                                                         | Weekly and monthly returns, annualized return, rolling volatility and Sharpe  |
| `backtesting` | `Strategy`, `StrategyFactory`, `Backtester`                               | Signal generation, the simulation loop, metrics                               |
| `auth`        | `AuthManager`                                                             | Accounts, password hashing, session tokens                                    |
| `core`        | `Config`, `Application`                                                   | Run-wide settings, and the facade that wires every engine together            |
| `utils`       | `Statistics`, `ThreadPool`, `CsvParser`, `Logger`, `Timer`, `Sha256`      | Shared building blocks                                                        |

### The Application facade

`Application` constructs the long-lived engines in dependency order and exposes them through accessors. Both the CLI and the REST server create exactly one:

```
Application
  market_      MarketDataEngine   shared_ptr
  portfolio_   Portfolio          shared_ptr   subscribed to market_ as a weak_ptr observer
  orders_      OrderManager       unique_ptr   holds a reference to portfolio_
  risk_        RiskEngine         value
  analytics_   AnalyticsEngine    value
  pool_        ThreadPool         value        sized to the hardware thread count
  auth_        AuthManager        unique_ptr   persists to <data directory>/users.json
  trade_mutex_ std::mutex                      serializes portfolio and order mutations
```

`bootstrap()` loads every `.csv` in the configured data directory and seeds each symbol's mark in the portfolio with its latest close, so valuation works before any trade.

### Design patterns in use

| Pattern    | Where                                       | Purpose                                                                             |
| :--------- | :------------------------------------------ | :---------------------------------------------------------------------------------- |
| Facade     | `Application`                               | One wiring shared by both front ends                                                |
| Strategy   | `Strategy` and its four implementations     | The backtester depends on an interface, not on trading logic                        |
| Factory    | `StrategyFactory`                           | Build strategies by enum or by name, identically from the CLI, API and tests        |
| Observer   | `MarketDataEngine` to `IMarketDataObserver` | The portfolio tracks marks; observers are `weak_ptr`, so a dead one is never called |
| Repository | `IMarketDataRepository`                     | Read access to bar history behind a stable query interface                          |
| Singleton  | `Config`                                    | Run-wide parameters behind a mutex-guarded accessor                                 |

## Life of a request

This is `POST /api/order`, the one endpoint that changes state:

```
client                  server_main.cpp                     engine
  | POST /api/order  JSON    |                                |
  |------------------------->| parse body                     |
  |                          | validate symbol, quantity      |
  |                          | latest_bar(symbol) ----------->| MarketDataEngine (shared lock)
  |                          | lock trade_mutex               |
  |                          | orders.submit(...) ----------->| OrderManager: id, status Submitted
  |                          | orders.process_next(close) --->| marketable? fill at the close
  |                          |                                | Portfolio.execute: cash, position, blotter
  |<-------------------------| {execution, portfolio}  JSON   |
```

Read-only endpoints (`/api/risk`, `/api/analytics`, `/api/backtest`) take no trade lock. They only read the market data, which is loaded once at startup and then immutable.

## Startup sequence of the REST server

1. Parse the arguments: port, data directory, web directory. See [Command line programs](cli.md#the-rest-server).
2. Set the data directory in `Config`. This must happen first, because `Application` builds the `AuthManager` from it.
3. Construct `Application`. The `AuthManager` loads `<data directory>/users.json` if it exists.
4. Call `bootstrap()`, which parses every CSV and logs `loaded SYMBOL: N bars, R rejected`.
5. Register the routes, then mount the web directory at `/` if it exists.
6. Listen on `0.0.0.0` at the chosen port.

## Concurrency model

| State                               | Protection                                                                                                            |
| :---------------------------------- | :-------------------------------------------------------------------------------------------------------------------- |
| Market data, observers, close cache | `std::shared_mutex`: many concurrent readers, exclusive writers                                                       |
| Portfolio and order book            | `Application::trade_mutex`, held by `/api/order`, `/api/undo` and `/api/portfolio`                                    |
| Accounts and sessions               | A mutex inside `AuthManager`                                                                                          |
| `Config`                            | A mutex inside the singleton, so worker threads can read it safely                                                    |
| Logger                              | Thread-safe by design                                                                                                 |
| Cross-sectional risk                | `ThreadPool` of `std::thread::hardware_concurrency()` workers (at least one), results collected through `std::future` |

The risk batch is the only place the engine parallelizes work. On a single-core machine it gives no speedup, and `/api/health` reports the thread count so you can tell.

## What is stored and what is not

| State                | Where it lives                                       | Survives a restart                |
| :------------------- | :--------------------------------------------------- | :-------------------------------- |
| Market data          | CSV files, loaded into memory at startup             | Yes (the files)                   |
| Accounts             | `<data directory>/users.json`                        | Yes                               |
| Session tokens       | Server memory                                        | No: everyone is signed out        |
| Portfolio and orders | Server memory                                        | No: resets to the initial capital |
| Trained AI models    | `code/ai_models/artifacts` or `ALPHAFORGE_MODEL_DIR` | Yes                               |
| AI service caches    | Service memory                                       | No                                |

**Note:** There is one portfolio per server process, not one per user. Every client, signed in or not, reads and trades the same account. See the [security notes](rest-api.md#security-notes).

Market data is read once at startup. To pick up new CSVs, restart the server, or in the CLI choose **Load Market Data**.

## The AI subsystem

### ai_models

A pipeline from CSV to a stored, versioned bundle of models, plus the code that evaluates them honestly:

```
SYMBOL.csv
   |  read_bars: parse, sort, validate
   v
bars (DatetimeIndex, open high low close volume)
   |  compute_features: 20 causal features, warm-up of 63 rows
   v
features ----------------------+--------------------------------------+
   |  make_dataset             |                                      |
   v                           v                                      v
features + label        DirectionModel                        RegimeDetector, AnomalyDetector
(up or down next day)   VolatilityForecaster                  (unsupervised, on slow features)
   |                           |                                      |
   +---------------------------+--------------------------------------+
                               v
                          ModelBundle  ----->  ModelRegistry (versioned, checksummed)
                               |
                               v
                          Predictor  ->  Prediction -> signal weight (SignalPolicy)
                               |
                               v
                  run_backtest (same cost model as the C++ engine)
```

`walk_forward_evaluate` repeats fit-then-predict over expanding windows with an embargo, and reports out-of-sample accuracy, AUC, volatility forecast error and strategy performance against buy and hold. The code is in `code/ai_models`.

### ai_services

A FastAPI application over `AiService`, which owns a model registry and two caches (loaded bundles, and recent backtests keyed by data fingerprint and parameters). On startup it optionally trains models that are missing or stale. Training is serialized by a lock, so a second concurrent request gets `409`. The code is in `code/ai_services`. Its endpoints are `/ai/health`, `/ai/models`, `/ai/predict/{symbol}`, `/ai/signals`, `/ai/history/{symbol}`, `/ai/backtest` and `/ai/train`, with interactive documentation at `/ai/docs`.

### Keeping the two halves consistent

The Python backtester must charge costs and compute metrics exactly like the C++ one, or a strategy would look different depending on where it was tested. A golden file captured from the real server pins them together: `code/ai_models/tests/golden/cpp_backtests.json`, and `code/ai_models/tests/test_cpp_parity.py` replays it through the Python backtester and requires the same equity curve and metrics.

## Boundaries and dependency rules

- `common` and `utils` depend on nothing else in the project.
- Engine modules never include `app/` headers. The JSON translation layer (`app/JsonSupport.hpp`) lives in the server so the engine stays free of HTTP and serialization concerns, with one exception: `AuthManager` uses nlohmann/json directly to persist accounts.
- The CLI and the server contain no financial logic. They parse input, call the facade, and format output.
- `ai_services` imports `ai_models`, never the reverse.
- Neither Python package depends on the C++ build. The coupling is the data directory and the golden file.
