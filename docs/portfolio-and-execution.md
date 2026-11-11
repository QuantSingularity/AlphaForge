# Portfolio and Execution

This document defines how AlphaForge accounts for positions, how orders are filled, and how a trade is undone. The behaviour is deliberately simple and fully deterministic. Several simplifications here, notably the absence of a cash check and fills at the latest close, are choices that matter, and each is called out where it applies.

## The model in one paragraph

There is one portfolio per server process. It starts with cash equal to the initial capital (1,000,000 by default). An order is submitted, immediately compared with the latest close of its symbol, and either filled at that close or rejected. A fill changes cash and one position, and is appended to a blotter. Undo removes the last fill and rebuilds the whole portfolio by replaying the rest.

## Positions

A position uses **average cost accounting**. Quantity is signed: positive is long, negative is short. Fractional quantities are allowed.

| Quantity        | Formula                             |
| :-------------- | :---------------------------------- |
| Cash delta      | `-signed_quantity * fill_price`     |
| Market value    | `quantity * mark`                   |
| Unrealized P&L  | `(mark - average_price) * quantity` |
| Position weight | `market_value / total_value`        |

`signed_quantity` is positive for a buy and negative for a sell. A short position has negative market value and its unrealized P&L has the right sign: selling 10 at 50 and marking at 45 gives a market value of -450 and an unrealized profit of +50.

### How a fill changes a position

| Situation                                | Effect                                                                                       |
| :--------------------------------------- | :------------------------------------------------------------------------------------------- |
| Opening, or adding in the same direction | Quantity grows. Average price becomes the quantity-weighted average of old cost and new fill |
| Reducing, without crossing zero          | Realized P&L is booked on the closed part. Average price is unchanged                        |
| Closing exactly                          | Realized P&L is booked, quantity becomes 0, average price resets to 0                        |
| Crossing zero (a flip)                   | Realized P&L is booked on the old position. The remainder opens at the fill price            |

Realized P&L on the closed quantity is `closed_quantity * (fill_price - average_price) * direction`, where `direction` is `+1` for a long and `-1` for a short.

### Worked example

Each row is one fill against a single symbol, starting from 10,000 cash. These numbers were produced by running the real `Portfolio` class.

| Step          | Quantity | Average price | Realized P&L |      Cash | What happened                                                           |
| :------------ | -------: | ------------: | -----------: | --------: | :---------------------------------------------------------------------- |
| Buy 100 at 10 |      100 |         10.00 |         0.00 |  9,000.00 | Opens a long                                                            |
| Buy 100 at 12 |      200 |         11.00 |         0.00 |  7,800.00 | Average cost is (100 x 10 + 100 x 12) / 200                             |
| Sell 50 at 13 |      150 |         11.00 |       100.00 |  8,450.00 | Closes 50 at a gain of 2 each; average cost is unchanged                |
| Sell 200 at 9 |      -50 |          9.00 |      -200.00 | 10,250.00 | Closes 150 at a loss of 2 each (-300), then flips to a short of 50 at 9 |
| Buy 50 at 8   |        0 |          0.00 |      -150.00 |  9,850.00 | Covers the short at a gain of 1 each (+50); position is flat            |

Final total value is 9,850: the initial 10,000 plus realized P&L of -150. With no open position there is no unrealized P&L.

## Portfolio valuation

All valuation uses **marks**, the latest known price of each symbol.

| Metric           | Definition                                                     |
| :--------------- | :------------------------------------------------------------- |
| `cash`           | Initial cash plus the cash delta of every fill                 |
| `holdings_value` | Sum of the market value of every non-flat position             |
| `total_value`    | `cash + holdings_value`                                        |
| `realized_pnl`   | Sum of realized P&L across all positions                       |
| `unrealized_pnl` | Sum of unrealized P&L across all non-flat positions            |
| `gross_exposure` | Sum of the absolute market values of non-flat positions        |
| `net_exposure`   | Sum of the signed market values of non-flat positions          |
| `weights`        | Each non-flat position's market value divided by `total_value` |

A position's mark is, in order of preference: the symbol's latest mark, or the position's average price if no mark exists. At startup `bootstrap()` seeds every symbol's mark with its latest close. Each fill also updates the mark of its symbol to the fill price.

**Note:** Marks do not move after startup. The engine loads history once and never receives live prices, so unrealized P&L reflects the last close in the data files.

## Orders

### Types and fields

| Field         | Meaning                            |
| :------------ | :--------------------------------- |
| `symbol`      | An instrument that has loaded data |
| `side`        | `buy` or `sell`                    |
| `type`        | `market`, `limit` or `stop`        |
| `quantity`    | Positive; fractional allowed       |
| `limit_price` | Used by limit orders               |
| `stop_price`  | Used by stop orders                |

Order ids are sequential integers starting at 1 for each server process.

### The fill model

The reference price is the **latest close** of the symbol. An order is **marketable** when:

| Type   | Buy                        | Sell                       |
| :----- | :------------------------- | :------------------------- |
| Market | always                     | always                     |
| Limit  | `reference <= limit_price` | `reference >= limit_price` |
| Stop   | `reference >= stop_price`  | `reference <= stop_price`  |

A marketable order fills **at the reference price**, whatever its limit or stop was. A limit buy at 200 with a close of 146.58 fills at 146.58. A non-marketable order is **rejected** with the reason `not marketable at reference price`. It is not left pending, so the pending count is always 0 after a request completes.

A limit or stop order with no price attached is never marketable, so it is rejected. The API does not return an error for the missing price.

### Order lifecycle

```
submit            process_next                  outcome
  |                    |
Submitted ---------> marketable? ---- yes ----> Filled     (fill recorded, added to trade history)
                       |
                       +------------ no -----> Rejected   (reason: not marketable at reference price)

undo ---------------------------------------> the filled order is marked Cancelled in the audit trail
```

The order manager keeps three structures: a FIFO queue of orders waiting to be processed, a stack of executed orders (the trade history, most recent on top), and a list of every order ever seen as an audit trail. Statuses are `PENDING`, `SUBMITTED`, `FILLED`, `CANCELLED` and `REJECTED`.

## No cash or margin check

**Note:** The engine does not check that you can afford an order or that you hold what you sell. This is verified behaviour, not an oversight in these documents:

- Buying more than your cash allows succeeds and drives cash negative. A test order for one billion shares of a 146 dollar stock filled, leaving cash at about negative 146 billion.
- Selling shares you do not own succeeds and opens a short position.
- There are no fees, no financing cost, no borrow cost and no margin requirements in the portfolio. (The backtester charges costs; the portfolio does not.)

If you need these constraints, add them before `OrderManager::submit` or in the server handler.

## Undo

`undo_last_trade()` reverses the most recent executed trade. It does not run the arithmetic backwards. It removes the last fill from the blotter, resets cash to the initial amount, clears all positions, and replays every remaining fill in order.

This is correct by construction, including for average-cost positions that flip sides, where inverse accounting would be error-prone. The cost is that undo is linear in the number of fills, which is irrelevant at this scale.

Properties you can rely on:

- Undoing restores the exact prior state: cash, quantities, average prices and realized P&L.
- You can undo repeatedly, back to the initial state. When nothing is left, `undone` is `false`.
- Undo reverses fills, not rejected orders, which never changed anything.
- The undone order is marked `CANCELLED` in the audit trail.

Verified on the real class: after the five fills above, two undos return to 150 shares at an average of 11.00, realized P&L of 100.00, cash of 8,450.00, and a blotter of three fills.

## The blotter

Every fill is recorded:

| Field        | Meaning                                                     |
| :----------- | :---------------------------------------------------------- |
| `symbol`     | Instrument                                                  |
| `side`       | `BUY` or `SELL`                                             |
| `quantity`   | Unsigned quantity filled                                    |
| `price`      | Fill price                                                  |
| `cash_delta` | Signed cash effect, negative for a buy                      |
| `timestamp`  | Informational; the REST server leaves it as an empty string |

## Concurrency

Order submission, undo and the portfolio snapshot all hold `Application::trade_mutex`, so concurrent requests cannot interleave a half-applied fill. See [Architecture](architecture.md#concurrency-model).

## Using it from each interface

| Interface | Order types | Notes                                                                      |
| :-------- | :---------- | :------------------------------------------------------------------------- |
| REST      | All three   | `POST /api/order`, `POST /api/undo`, `GET /api/portfolio`                  |
| Web       | All three   | The Order Ticket panel, with an Undo Last button, and the Portfolio panel  |
| CLI       | Market only | Options 4 (Submit Order), 3 (Portfolio Dashboard) and 10 (Undo Last Trade) |

The REST details and examples are in [REST API reference](rest-api.md#post-apiorder).
