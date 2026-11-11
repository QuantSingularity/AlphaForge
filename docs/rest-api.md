# REST API Reference

The C++ server exposes the engine as a JSON API under `/api`. This reference covers every endpoint. Every request and response shown was captured from the running server, using synthetic prices.

The separate Python service in `code/ai_services` has its own API under `/ai` on port 8090 and is not covered here.

## Overview

| Item           | Value                                                                                                        |
| :------------- | :----------------------------------------------------------------------------------------------------------- |
| Base URL       | `http://localhost:8080` (the port is configurable)                                                           |
| Format         | JSON in, JSON out. Responses use `application/json`                                                          |
| Request bodies | Send `Content-Type: application/json`. The server parses the body either way                                 |
| Authentication | Sessions exist, but **no data or trading endpoint requires one**. See below                                  |
| CORS           | Every response allows any origin (`*`), the methods `GET, POST, OPTIONS`, and the header `Content-Type` only |
| Key order      | Object keys come back in alphabetical order. Do not rely on any order                                        |

### Endpoint summary

| Method | Path                      | Purpose                                             |
| :----- | :------------------------ | :-------------------------------------------------- |
| GET    | `/api/health`             | Status, version, symbols loaded, thread count       |
| GET    | `/api/strategies`         | Strategy names                                      |
| GET    | `/api/symbols`            | Loaded symbols and their bar counts                 |
| GET    | `/api/market/{symbol}`    | Price bars for a symbol                             |
| GET    | `/api/portfolio`          | Portfolio snapshot and trade blotter                |
| POST   | `/api/order`              | Submit an order, filled against the latest close    |
| POST   | `/api/undo`               | Reverse the most recent trade                       |
| GET    | `/api/risk/{symbol}`      | Risk metrics for one symbol                         |
| GET    | `/api/risk`               | Risk metrics for every symbol, computed in parallel |
| GET    | `/api/analytics/{symbol}` | Returns and rolling statistics                      |
| POST   | `/api/backtest`           | Run a strategy backtest                             |
| POST   | `/api/auth/register`      | Create an account and open a session                |
| POST   | `/api/auth/login`         | Open a session                                      |
| POST   | `/api/auth/logout`        | End a session                                       |
| GET    | `/api/auth/me`            | The user for a session token                        |

The four `/api/auth/*` routes exist in the server but are not in the top-level README's table.

### Cross-origin use

The allowed header list is just `Content-Type`. A page served from another origin can call the API, but the browser's preflight check will refuse a request that adds an `Authorization` header, so such a page cannot send a bearer token. The web terminal avoids this by being served from the same origin as the API (or through the Vite proxy in development). Non-browser clients such as `curl` and scripts are unaffected.

### Authentication, in one paragraph

Sign in returns a bearer token. Only `/api/auth/me` and `/api/auth/logout` read it. Every other endpoint, including `POST /api/order`, works without any token, and the one portfolio is shared by every caller. The web terminal's sign-in gate is enforced in the browser, not by the API. Read [Security notes](#security-notes) before exposing the server beyond your own machine.

### Errors

Errors from the handlers have this shape:

```json
{ "error": "unknown symbol" }
```

| Status | Meaning                                                                                                                  |
| :----- | :----------------------------------------------------------------------------------------------------------------------- |
| 200    | Success                                                                                                                  |
| 201    | Account created (`/api/auth/register`)                                                                                   |
| 204    | Response to an `OPTIONS` preflight                                                                                       |
| 400    | Invalid JSON body, a failed validation, or an unknown strategy                                                           |
| 401    | Missing or invalid credentials or session (`/api/auth/*`)                                                                |
| 404    | Unknown symbol                                                                                                           |
| 409    | The symbol is listed but has no price (`no price available for symbol`). A CSV with a header and no valid rows does this |
| 500    | A request field had the wrong JSON type. The body is **empty**, not JSON                                                 |

Three behaviours are easy to trip over:

- **An unknown route, or the wrong method on a known route, returns `404` with an empty body**, not a JSON error and not `405`. For example `GET /api/order` is a 404.
- **A field of the wrong JSON type returns `500` with an empty body.** Sending `"quantity": "10"` (a string) to `/api/order`, `"symbol": 5`, `"strategy": 5` or `"transaction_cost_bps": "5"` to `/api/backtest` all do this. The server keeps running. Send numbers as JSON numbers and strings as JSON strings.
- **Malformed query parameters are silently ignored** and the default is used. See each endpoint.

Symbols are case-sensitive and must match a file name exactly. `GET /api/market/aaa` is a 404 when the file is `AAA.csv`.

## GET /api/health

```bash
curl http://localhost:8080/api/health
```

```json
{ "status": "ok", "symbols_loaded": 2, "threads": 1, "version": "1.0.0" }
```

| Field            | Meaning                                                                            |
| :--------------- | :--------------------------------------------------------------------------------- |
| `status`         | Always `"ok"` if the server answers                                                |
| `version`        | Engine version                                                                     |
| `symbols_loaded` | Number of symbols with loaded data. `0` means the data directory is empty or wrong |
| `threads`        | Size of the thread pool, the hardware thread count                                 |

## GET /api/strategies

```json
{
  "strategies": [
    "BuyAndHold",
    "MovingAverageCrossover",
    "Momentum",
    "MeanReversion"
  ]
}
```

## GET /api/symbols

```json
{
  "symbols": [
    { "bars": 600, "symbol": "AAA" },
    { "bars": 600, "symbol": "BBB" }
  ]
}
```

Sorted by symbol.

## GET /api/market/{symbol}

| Parameter | Where | Default | Meaning                                         |
| :-------- | :---- | :------ | :---------------------------------------------- |
| `symbol`  | path  |         | A loaded symbol. Letters, digits, `_`, `.`, `-` |
| `limit`   | query | all     | Return only the most recent N bars              |

```bash
curl "http://localhost:8080/api/market/AAA?limit=2"
```

```json
{
  "bars": [
    {
      "close": 144.8542,
      "date": "2024-04-18",
      "high": 145.7608,
      "low": 144.2828,
      "open": 145.1881,
      "volume": 1225184
    },
    {
      "close": 146.5796,
      "date": "2024-04-19",
      "high": 148.3634,
      "low": 142.7942,
      "open": 144.5534,
      "volume": 891252
    }
  ],
  "symbol": "AAA"
}
```

Bars are in ascending date order. A `limit` that is zero, negative, not a number, or at least the number of bars returns the **full history**. Without a `limit`, a long history makes a large response.

## GET /api/portfolio

The current portfolio and the blotter of every fill. An unused portfolio looks like this:

```json
{
  "blotter": [],
  "cash": 1000000.0,
  "executed_orders": 0,
  "gross_exposure": 0.0,
  "holdings_value": 0.0,
  "initial_cash": 1000000.0,
  "net_exposure": 0.0,
  "pending_orders": 0,
  "positions": [],
  "realized_pnl": 0.0,
  "total_value": 1000000.0,
  "unrealized_pnl": 0.0,
  "weights": []
}
```

| Field                            | Meaning                                                                                                                          |
| :------------------------------- | :------------------------------------------------------------------------------------------------------------------------------- |
| `cash`, `initial_cash`           | Current and starting cash                                                                                                        |
| `holdings_value`, `total_value`  | Value of positions, and cash plus that                                                                                           |
| `realized_pnl`, `unrealized_pnl` | Booked and open profit and loss                                                                                                  |
| `gross_exposure`, `net_exposure` | Sum of absolute and of signed position values                                                                                    |
| `positions`                      | Each open position: `symbol`, `quantity`, `avg_price`, `realized_pnl`, and when marked, `mark`, `market_value`, `unrealized_pnl` |
| `weights`                        | Each position's share of `total_value`                                                                                           |
| `blotter`                        | Every fill: `symbol`, `side`, `quantity`, `price`, `cash_delta`, `timestamp`                                                     |
| `pending_orders`                 | Orders waiting to be processed. Always 0 after a request completes                                                               |
| `executed_orders`                | Number of fills currently in the trade history (reduced by undo)                                                                 |

The accounting is defined in [Portfolio and execution](portfolio-and-execution.md).

## POST /api/order

Submits an order and processes it immediately against the latest close of its symbol.

| Field         | Type   | Required | Default    | Notes                                                                 |
| :------------ | :----- | :------- | :--------- | :-------------------------------------------------------------------- |
| `symbol`      | string | yes      |            | A loaded symbol, case-sensitive                                       |
| `quantity`    | number | yes      |            | Must be greater than 0                                                |
| `side`        | string | no       | `"buy"`    | **`"sell"` or `"SELL"` sells. Anything else buys**                    |
| `type`        | string | no       | `"market"` | `"limit"` or `"LIMIT"`, `"stop"` or `"STOP"`. Anything else is market |
| `limit_price` | number | no       |            | Used by limit orders                                                  |
| `stop_price`  | number | no       |            | Used by stop orders                                                   |

**Note:** `side` and `type` recognise exactly two spellings each, all lower case or all upper case. This is verified behaviour:

| You send                             | The server does  |
| :----------------------------------- | :--------------- |
| `"side": "sell"` or `"SELL"`         | Sell             |
| `"side": "Sell"`                     | **Buy**          |
| `"side": "short"` or `"sel"` or `""` | **Buy**          |
| `"type": "limit"` or `"LIMIT"`       | Limit order      |
| `"type": "Limit"` or `"limt"`        | **Market order** |

A typo in `side` therefore opens a long position instead of failing, and a typo in `type` ignores your price limit. Send the exact lower-case spellings and check the response.

```bash
curl -X POST http://localhost:8080/api/order \
  -H "Content-Type: application/json" \
  -d '{"symbol":"AAA","side":"buy","type":"market","quantity":100}'
```

```json
{
  "execution": {
    "fill_price": 146.5796,
    "order_id": 1,
    "reason": "",
    "status": "FILLED"
  },
  "portfolio": {
    "cash": 985342.04,
    "gross_exposure": 14657.96,
    "holdings_value": 14657.96,
    "initial_cash": 1000000.0,
    "net_exposure": 14657.96,
    "positions": [
      {
        "avg_price": 146.5796,
        "mark": 146.5796,
        "market_value": 14657.96,
        "quantity": 100.0,
        "realized_pnl": 0.0,
        "symbol": "AAA",
        "unrealized_pnl": 0.0
      }
    ],
    "realized_pnl": 0.0,
    "total_value": 1000000.0,
    "unrealized_pnl": 0.0,
    "weights": [{ "symbol": "AAA", "weight": 0.01465796 }]
  }
}
```

A limit order priced below the market is rejected, and the response is still `200`:

```json
{
  "execution": {
    "order_id": 2,
    "reason": "not marketable at reference price",
    "status": "REJECTED"
  },
  "portfolio": { "...": "unchanged" }
}
```

The `portfolio` object in an order or undo response is a compact snapshot. It has the valuation fields, `positions` and `weights`, but not `blotter`, `pending_orders` or `executed_orders`. Call `GET /api/portfolio` for those.

`fill_price` is present only for a fill. `status` is `FILLED` or `REJECTED`. A rejection is a normal outcome, not an HTTP error. Check `execution.status`, not the HTTP status, to know whether you traded.

| Status | When                                                                          |
| :----- | :---------------------------------------------------------------------------- |
| 400    | Body is not valid JSON, or `symbol` is empty, or `quantity` is not above zero |
| 404    | The symbol is not loaded                                                      |
| 409    | The symbol has no latest price                                                |

Fills happen at the latest close whatever the limit or stop price was. There is no cash check, so an order larger than your cash succeeds and drives cash negative. See [Portfolio and execution](portfolio-and-execution.md#no-cash-or-margin-check).

## POST /api/undo

Reverses the most recent filled trade. No request body is needed, but send `{}`.

```json
{
  "portfolio": { "...": "the portfolio after undo" },
  "undone": true
}
```

`undone` is `false` when there is nothing to undo.

## GET /api/risk/{symbol}

| Parameter    | Where | Default | Meaning                                                    |
| :----------- | :---- | :------ | :--------------------------------------------------------- |
| `symbol`     | path  |         | A loaded symbol                                            |
| `confidence` | query | `0.95`  | VaR and CVaR confidence. A non-number falls back to `0.95` |
| `benchmark`  | query | none    | Another loaded symbol, for beta                            |

```bash
curl "http://localhost:8080/api/risk/AAA?confidence=0.99&benchmark=BBB"
```

```json
{
  "annualized_volatility": 0.20788759732798104,
  "beta": 0.1038181734952544,
  "conditional_var": 0.04226055422964744,
  "confidence": 0.99,
  "max_drawdown": 0.14648698080444056,
  "samples": 599,
  "sharpe_ratio": 0.7804059525278532,
  "sortino_ratio": 0.78481239451656,
  "symbol": "AAA",
  "value_at_risk": 0.03312850812952238
}
```

`beta` is `null` without a benchmark, with an unknown benchmark, or when the benchmark has fewer bars than the symbol. `samples` is the number of returns used. The confidence is not range-checked, so use a value strictly between 0 and 1. Every formula is in [Risk and analytics](risk-and-analytics.md).

## GET /api/risk

Risk for every loaded symbol, computed in parallel on the thread pool and sorted by symbol. It takes `confidence` as above and does not compute beta.

```json
{
  "confidence": 0.95,
  "risk": [
    {
      "annualized_volatility": 0.20788759732798104,
      "beta": null,
      "conditional_var": 0.02782546173790553,
      "max_drawdown": 0.14648698080444056,
      "sharpe_ratio": 0.7804059525278532,
      "sortino_ratio": 0.78481239451656,
      "symbol": "AAA",
      "value_at_risk": 0.019238483081742915
    }
  ]
}
```

The array has one entry per loaded symbol (abridged here).

## GET /api/analytics/{symbol}

| Parameter | Where | Default | Meaning                                               |
| :-------- | :---- | :------ | :---------------------------------------------------- |
| `symbol`  | path  |         | A loaded symbol                                       |
| `window`  | query | `20`    | Rolling window in bars. A non-number falls back to 20 |

```json
{
  "annualized_return": 0.174221608165811,
  "monthly_returns": [{ "label": "2022-02", "value": -0.05526850061099409 }],
  "rolling_sharpe": [{ "label": "2022-01-31", "value": 3.5112413351606215 }],
  "rolling_volatility": [
    { "label": "2022-01-31", "value": 0.13280562178524116 }
  ],
  "symbol": "AAA",
  "weekly_returns": [{ "label": "2022-W02", "value": -0.009319012590820908 }],
  "window": 20
}
```

Each series is an array of `{label, value}` points, and the first point of each is shown here. For a 600-bar series with `window=20`, the response has 27 monthly points, 119 weekly points, and 580 points in each rolling series. A window below 2, or at least the number of bars, gives empty rolling arrays. Definitions are in [Risk and analytics](risk-and-analytics.md#analytics).

## POST /api/backtest

| Field                  | Type   | Required | Default        | Notes                                                                          |
| :--------------------- | :----- | :------- | :------------- | :----------------------------------------------------------------------------- |
| `symbol`               | string | yes      |                | A loaded symbol                                                                |
| `strategy`             | string | no       | `"BuyAndHold"` | A name or alias; see [Backtesting](backtesting.md#choosing-a-strategy-by-name) |
| `initial_capital`      | number | no       | 1,000,000      | Not validated                                                                  |
| `transaction_cost_bps` | number | no       | 5              | Not validated                                                                  |
| `slippage_bps`         | number | no       | 2              | Not validated                                                                  |

```bash
curl -X POST http://localhost:8080/api/backtest \
  -H "Content-Type: application/json" \
  -d '{"symbol":"AAA","strategy":"MovingAverageCrossover"}'
```

The response has `strategy`, `symbol`, `equity_curve`, `metrics` and `benchmark`:

```json
{
  "benchmark": {
    "annualized_volatility": 0.20786975225766371,
    "cagr": 0.17388039840270797,
    "max_drawdown": 0.14648698080444048,
    "num_trades": 1,
    "profit_factor": 1.1523333779866138,
    "sharpe_ratio": 0.7790562399511848,
    "sortino_ratio": 0.7833878092172255,
    "total_return": 0.46384397028902113,
    "win_rate": 0.5275459098497496
  },
  "equity_curve": [
    { "date": "2022-01-03", "equity": 1000000.0, "target_weight": 0.0 }
  ],
  "metrics": {
    "annualized_volatility": 0.16679732338717604,
    "cagr": 0.06766593380544128,
    "max_drawdown": 0.150308097849447,
    "num_trades": 14,
    "profit_factor": 1.101311361760455,
    "sharpe_ratio": 0.35594620696420765,
    "sortino_ratio": 0.42376007829762374,
    "total_return": 0.16839707420675154,
    "win_rate": 0.5116883116883116
  },
  "strategy": "MovingAverageCrossover",
  "symbol": "AAA"
}
```

`equity_curve` has one point per bar (600 here, one shown). The overrides are **not range-checked**: a negative `initial_capital` is accepted and produces a negative equity curve. Validate values on your side.

| Status | When                                      |
| :----- | :---------------------------------------- |
| 400    | Invalid JSON body, or an unknown strategy |
| 404    | The symbol is not loaded                  |

An empty `symbol` is a 404 (`unknown symbol`). The simulation is defined in [Backtesting](backtesting.md#how-a-backtest-runs).

## Authentication endpoints

### POST /api/auth/register

| Field      | Rule                                                                     |
| :--------- | :----------------------------------------------------------------------- |
| `name`     | At least 2 characters after trimming                                     |
| `email`    | Contains `@` with text on both sides, a `.` after the `@`, and no spaces |
| `password` | At least 8 characters                                                    |

```json
{
  "token": "4050dbda...",
  "user": {
    "created_at": "2026-11-10T19:25:09Z",
    "email": "Ada@Example.com",
    "id": "1",
    "name": "Ada Lovelace"
  }
}
```

Status `201`. The token is 64 hexadecimal characters (shortened above). Emails are matched case-insensitively, so registering `ada@example.com` after `Ada@Example.com` fails, while the account keeps the spelling it was created with.

| Status | `error`                                     |
| :----- | :------------------------------------------ |
| 400    | `name must be at least 2 characters`        |
| 400    | `enter a valid email address`               |
| 400    | `password must be at least 8 characters`    |
| 400    | `an account with that email already exists` |
| 400    | `invalid JSON body`                         |

### POST /api/auth/login

Body: `email` and `password`. Success is `200` with the same `{token, user}` shape. Email matching is case-insensitive. Failure is `401` with `invalid email or password`, whether the email is unknown or the password is wrong, so the response does not reveal which accounts exist.

### POST /api/auth/logout

Send the token as `Authorization: Bearer <token>`. Returns `{"ok": true}` whether or not the token was valid.

### GET /api/auth/me

```bash
curl http://localhost:8080/api/auth/me -H "Authorization: Bearer <token>"
```

```json
{
  "user": {
    "created_at": "2026-11-10T19:25:09Z",
    "email": "Ada@Example.com",
    "id": "1",
    "name": "Ada Lovelace"
  }
}
```

Without a valid token: `401` with `{"error": "not authenticated"}`. After logout, the token no longer works.

## Security notes

The account system exists so the web terminal can have a sign-in page. It is not an access control layer for the API. Everything in this section was verified against the source or the running server. Read it before exposing the server beyond your own machine.

### What is and is not protected

| Capability                                   | Needs a session token?       |
| :------------------------------------------- | :--------------------------- |
| Read market data, risk, analytics, backtests | No                           |
| Place orders, undo trades                    | No                           |
| Read the portfolio and blotter               | No                           |
| `GET /api/auth/me`                           | Yes, otherwise `401`         |
| `POST /api/auth/logout`                      | Reads the token, never fails |

There is one portfolio per server process. Every caller, signed in or not, sees and changes the same account, so accounts do not separate users' trading.

### How accounts are stored

Registration writes `users.json` into the server's data directory, as a JSON array. Each entry holds `id`, `name`, `email` (lower-cased), `display_email` (the spelling the user typed), `salt` (32 hex characters), `password_hash` (64 hex characters) and `created_at`. Passwords are never stored in plain text.

- **The file is not git-ignored.** `.gitignore` covers `code/backend/data/*.csv` only. A `git add .` after anyone registers will stage the password hashes. Add `code/backend/data/users.json` to `.gitignore` before committing.
- **A file that cannot be parsed is treated as empty.** The server logs `failed to parse ... starting fresh` and starts with no accounts. The next registration then overwrites the unreadable file, so keep a backup before editing it by hand.
- **Writes truncate and rewrite the whole file**, not atomically. A crash during a write can leave it corrupt.

### Passwords and tokens

| Item             | Implementation                                                                                                |
| :--------------- | :------------------------------------------------------------------------------------------------------------ |
| Password hashing | A random salt, then SHA-256 iterated 20,000 times. The comparison is constant-time                            |
| Session tokens   | 32 random bytes as 64 hex characters, kept in server memory, with no expiry. Logout or a restart removes them |
| Randomness       | `std::mt19937_64` seeded once from a single `std::random_device` value, used for both tokens and salts        |

Limits of this design:

- **Iterated SHA-256 is not a memory-hard password hash.** It is much cheaper to attack on GPUs than bcrypt, scrypt or Argon2.
- **The random generator is not cryptographically secure**, and it is seeded with one 32-bit value on common platforms, so the whole stream of tokens and salts comes from a small seed space. Do not rely on these tokens to guard anything of value.
- **Login timing reveals whether an email is registered.** An unknown email returns immediately, while a known one first runs the 20,000 hash rounds. Measured locally, a wrong password for a registered email took about 18 ms and an unknown email about 0.3 ms, a roughly 70 times difference that is easy to detect. The error message itself is identical for both.
- **There is no rate limiting or lockout** on login or registration.
- **Tokens never expire** until the process restarts.

### Transport and the browser

- The server speaks plain HTTP, with no TLS, and listens on all interfaces (`0.0.0.0`).
- CORS allows any origin, but only the `Content-Type` header, so a page from another origin cannot send the bearer token.
- The web terminal keeps the token in `localStorage`, which any script running on the page can read. A cross-site scripting flaw would expose it.

### The AI service and stored models

The Python service has no authentication at all. It listens on `127.0.0.1:8090` by default, and the Docker image changes that to `0.0.0.0` for container networking. Setting `AI_ALLOW_TRAINING=0` disables `POST /ai/train`.

Trained models are stored as Python pickles through `joblib`. Loading a pickle can run arbitrary code, so only load a model directory you control. The SHA-256 checksum in a model's metadata detects corruption, but it is stored next to the file, so it does not protect against someone who can replace both.

### If you need to expose it

- Keep the server on `localhost` or a private network, and put a reverse proxy in front of it that provides TLS and the authentication you need.
- If you want per-user access, add token checks to the handlers. The helper `bearer_token` and `AuthManager::user_for_token` already exist for this, and the portfolio would need to become per user.
- Replace the password hash with Argon2id, bcrypt or scrypt, generate tokens from the operating system's secure random source, and give them an expiry.
- Add `code/backend/data/users.json` to `.gitignore`.

## Static files

If the web directory given to the server exists, it is served at `/`. A path that is not under `/api` and is not a file falls back to `index.html`, so client-side routes such as `/terminal` load. Unknown `/api` paths are not rewritten: they return the empty `404`.

## Calling the API from code

A minimal Python client:

```python
import json
import urllib.request

BASE = "http://localhost:8080"


def post(path, body):
    request = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read())


result = post("/api/backtest", {"symbol": "AAA", "strategy": "sma"})
print(result["metrics"]["total_return"], result["benchmark"]["total_return"])
```

`urllib` raises `HTTPError` for any non-2xx status. For a `500` with an empty body, `json.loads` on the body will fail, so check the status before decoding.
