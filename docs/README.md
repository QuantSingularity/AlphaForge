# AlphaForge Documentation

This directory is the reference documentation for AlphaForge: a C++20 quantitative trading and research engine, its REST API and web terminal, and two Python components that add machine learning models and an HTTP service for them.

The top-level [README](../README.md) is the short tour. These documents go into depth, and every example in them was produced by running the real code.

## Start here

| If you want to                                         | Read                                  |
| :----------------------------------------------------- | :------------------------------------ |
| Install, build and run the platform for the first time | [Getting started](getting-started.md) |
| Understand how the pieces fit together                 | [Architecture](architecture.md)       |
| Call the REST API                                      | [REST API reference](rest-api.md)     |

## All documents

### Using the platform

| Document                              | What it covers                                                                                 |
| :------------------------------------ | :--------------------------------------------------------------------------------------------- |
| [Getting started](getting-started.md) | Prerequisites, fetching data, building, running the stack, a first session with each interface |
| [Data](data.md)                       | The CSV format, validation rules in the engine and in Python, fetching and refreshing data     |
| [CLI](cli.md)                         | The interactive terminal, the server's command line, the benchmark harness                     |
| [Web terminal](frontend.md)           | The React application: pages, panels, API client, sign in, building and testing                |

### How it works

| Document                                              | What it covers                                                                       |
| :---------------------------------------------------- | :----------------------------------------------------------------------------------- |
| [Architecture](architecture.md)                       | Components, data flow, startup, concurrency model, what is persisted and what is not |
| [Portfolio and execution](portfolio-and-execution.md) | Position accounting, order types, the fill model, undo                               |
| [Risk and analytics](risk-and-analytics.md)           | The definition and formula of every risk and analytics metric                        |
| [Backtesting](backtesting.md)                         | The simulation loop, cost model, the four strategies, performance metrics            |
| [REST API reference](rest-api.md)                     | Every endpoint of the C++ server with request and response examples                  |

## The platform at a glance

| Component      | Language and stack                | Default address         | Purpose                                                         |
| :------------- | :-------------------------------- | :---------------------- | :-------------------------------------------------------------- |
| Engine library | C++20                             | none (static library)   | Market data, portfolio, execution, risk, analytics, backtesting |
| REST server    | C++20, cpp-httplib, nlohmann/json | `http://localhost:8080` | JSON API, accounts, serves the built web terminal               |
| CLI terminal   | C++20                             | none (stdin and stdout) | Menu-driven access to the same engine                           |
| Web terminal   | React, TypeScript, Vite, Recharts | served by the server    | Charts and panels over the REST API                             |
| `ai_models`    | Python, scikit-learn, pandas      | none (library and CLI)  | Direction, volatility, regime and anomaly models                |
| `ai_services`  | Python, FastAPI, uvicorn          | `http://127.0.0.1:8090` | HTTP service over `ai_models`                                   |

Two facts about this layout are worth knowing up front, because they are easy to assume the other way around:

- The AI service is a separate process. Neither the web terminal nor the C++ server calls it today. The two sides share only the directory of CSV files. See [Architecture](architecture.md).
- The C++ engine and the Python backtester are held to the same arithmetic by a golden-file test, so a strategy backtested in either gives the same equity curve.

## Conventions used in these documents

- Paths are relative to the repository root unless a command begins with `cd`.
- Shell examples assume a Unix-like system and `bash`.
- The numbers in examples (prices, returns, Sharpe ratios, model scores) come from synthetic price series generated for the purpose, not from real markets. They show the shape of the output, not what to expect from your data.
- Where a document describes behaviour that might surprise you, it says so in a note beginning with **Note**.
- Where the code and the top-level README disagree, these documents follow the code and call out the difference.
