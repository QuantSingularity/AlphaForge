# Backtesting

The backtester runs a trading strategy over the history of one instrument and reports how it would have performed, net of costs, next to a buy-and-hold benchmark over the same bars. This document defines the simulation exactly, so that you can reproduce any result by hand.

The description here was verified by reimplementing the four strategies and the simulation loop independently and comparing with the C++ server. The equity curves matched to the last digit, with identical trade counts.

## How a backtest runs

A backtest takes a strategy, a series of bars and a configuration:

| Setting                | Default   | Meaning                                        |
| :--------------------- | :-------- | :--------------------------------------------- |
| `initial_capital`      | 1,000,000 | Starting equity                                |
| `transaction_cost_bps` | 5         | Cost per unit of turnover, in basis points     |
| `slippage_bps`         | 2         | Slippage per unit of turnover, in basis points |

One basis point is 0.01%. The defaults come from `Config`, and a REST request can override each of the three. See [REST API reference](rest-api.md#post-apibacktest).

### The simulation loop

The strategy produces one target weight per bar. The loop then walks the bars once:

```
cost_rate   = (transaction_cost_bps + slippage_bps) / 10000
equity      = initial_capital
prev_weight = 0

curve[0] = (date[0], equity, signal[0])

for i in 1 .. N-1:
    new_weight   = signal[i-1]                      decided with data up to bar i-1
    turnover     = abs(new_weight - prev_weight)
    cost         = turnover * cost_rate
    asset_return = close[i] / close[i-1] - 1
    period_return = new_weight * asset_return - cost
    equity       = equity * (1 + period_return)
    if turnover > 1e-9: trades += 1
    prev_weight  = new_weight
    curve[i]     = (date[i], equity, new_weight)
```

### No look-ahead

The weight applied to the move from bar `i-1` to bar `i` is `signal[i-1]`, the signal computed from data **up to and including bar `i-1`**. The strategy never sees the return it is about to earn. A signal that turns long at the close of Monday earns Tuesday's return, not Monday's.

### Costs

Cost is charged on **turnover**, the absolute change in weight, in the bar where the position changes:

- Going from flat to fully invested has a turnover of 1, so it costs `cost_rate` once. With the defaults that is 7 basis points.
- Going from fully invested to flat costs the same again.
- Staying in the same position costs nothing.

The cost is subtracted from that bar's return. It is a proportional cost on the notional traded, so a strategy that trades often pays more.

Costs make a measurable difference. For a buy-and-hold on a 600-bar series, setting both costs to zero gives a total return of exactly the plain price return, `close[last] / close[first] - 1 = 0.46486`. With the default 7 basis points, the return is `0.46384`, a drop of about 7 basis points of final value from the single entry trade.

### The equity curve

`equity_curve` has one point per bar, so a 600-bar series gives 600 points.

| Field           | Meaning                                                                                  |
| :-------------- | :--------------------------------------------------------------------------------------- |
| `date`          | The bar's date                                                                           |
| `equity`        | Equity after that bar                                                                    |
| `target_weight` | The weight held **over the move ending at that bar**, which is the previous bar's signal |

The first point is the starting equity, and its `target_weight` is `signal[0]`, an exception to the rule above because nothing was held before it. If you plot `target_weight` against the price, it lags the signal by one bar. That is the no-look-ahead rule made visible.

### The benchmark

The benchmark is buy and hold, run with the same costs over the identical bars. Its `num_trades` is 1, the entry. It is reported in the same metric fields as the strategy so the two compare directly.

## Metrics

All metrics are computed from the strategy's daily return series (the period returns of the equity curve) and from the equity curve itself.

| Metric                  | Definition                                                                                      |
| :---------------------- | :---------------------------------------------------------------------------------------------- |
| `total_return`          | `final_equity / initial_equity - 1`                                                             |
| `cagr`                  | `(final / initial) ^ (1 / years) - 1`, with `years = number_of_returns / 252`                   |
| `annualized_volatility` | Sample stdev of the daily returns times `sqrt(252)`                                             |
| `sharpe_ratio`          | As in [Risk and analytics](risk-and-analytics.md#sharpe-ratio), on the strategy's daily returns |
| `sortino_ratio`         | As in [Risk and analytics](risk-and-analytics.md#sortino-ratio)                                 |
| `max_drawdown`          | Largest peak-to-trough fall of the **equity curve**, as a fraction of the peak                  |
| `win_rate`              | Days with a positive return, divided by days with a non-zero return                             |
| `profit_factor`         | Sum of the gains divided by the sum of the losses, capped at 99.99                              |
| `num_trades`            | Number of bars on which the target weight changed                                               |

Notes on the less obvious ones:

- **Flat days count in the return series.** A strategy that is out of the market earns exactly zero on those days. This lowers its volatility, and lowers its Sharpe ratio if the days it missed were good ones. This is correct, but it means a strategy and its benchmark are compared over the same calendar, not the same number of invested days.
- **`win_rate` ignores flat days.** A day counts only if its return has an absolute value above `1e-12`.
- **`profit_factor` is capped.** If there are no losing days at all it is reported as 99.99, as is any ratio above that.
- **`num_trades` includes the entry.** A single entry that is never exited is one trade.
- **Metrics are zero for a degenerate run.** With fewer than two bars there is no curve and everything is zero.

## Strategies

A strategy maps the bar history to a target weight for every bar. The interface allows weights from -1 (fully short) to 1 (fully long). All four built-in strategies are long-only, with weights of 0 or 1.

| Strategy                 | Rule                                                                            | Defaults                           |
| :----------------------- | :------------------------------------------------------------------------------ | :--------------------------------- |
| `BuyAndHold`             | Weight 1 on every bar                                                           | none                               |
| `MovingAverageCrossover` | Weight 1 when the fast simple moving average is above the slow one, otherwise 0 | fast 20, slow 50                   |
| `Momentum`               | Weight 1 when the return over the lookback is positive, otherwise 0             | lookback 63                        |
| `MeanReversion`          | Enter on a price stretched below its mean, exit when it reverts                 | window 20, entry z 1.0, exit z 0.0 |

### BuyAndHold

The benchmark every other strategy is measured against.

### MovingAverageCrossover

The weight is 0 until `slow` bars of history exist, because the slow average is undefined before then. After that it is 1 when `SMA(fast) > SMA(slow)` and 0 otherwise. The comparison is strict, so equal averages give 0.

### Momentum

For bar `i >= lookback`, the trailing return is `close[i] / close[i - lookback] - 1`. The weight is 1 when it is above zero, 0 otherwise, and 0 for the first `lookback` bars.

### MeanReversion

A state machine with memory. It holds a position (0 or 1) that persists across bars:

```
z = (close[i] - mean(last window closes)) / stdev(last window closes)      sample stdev

if flat and z < -entry_z:        go long
if long and z >= -exit_z:        go flat
otherwise:                       keep the current position
```

During the first `window - 1` bars the position is held at its initial value of 0. A window with zero standard deviation leaves the position unchanged. With the defaults, it buys when the price is more than one standard deviation below its 20-day mean and sells when it returns to the mean.

### Choosing a strategy by name

The REST API and CLI accept the full name or an alias, in any letter case:

| Strategy                 | Names accepted                                       |
| :----------------------- | :--------------------------------------------------- |
| `BuyAndHold`             | `buyandhold`, `buy_and_hold`, `bh`                   |
| `MovingAverageCrossover` | `movingaveragecrossover`, `macrossover`, `ma`, `sma` |
| `Momentum`               | `momentum`, `mom`                                    |
| `MeanReversion`          | `meanreversion`, `meanrev`, `mr`                     |

The response always reports the canonical name. An unknown name returns `400 unknown strategy`. If a REST request names no strategy, it runs `BuyAndHold`.

**Note:** Strategy parameters are not configurable through the API or the CLI. The factory builds each strategy with its defaults. To change a window or threshold, construct the strategy in code, or change the defaults in `code/backend/include/backtesting/Strategy.hpp`.

To add a new strategy, implement the `Strategy` interface (`name()` and `generate_signals()`, declared in the same header), add a value to the `StrategyType` enum in `code/backend/include/common/Types.hpp`, and register it in `code/backend/src/StrategyFactory.cpp`. The factory provides `create`, `parse` and `available`, so once it is registered the CLI and the REST API pick the strategy up by name with no further changes.

## One implementation, two front ends

The CLI and the REST server call the same `Backtester`, so they return the same numbers. For the same 600-bar series and the default settings, `MovingAverageCrossover` gave:

| Metric       | CLI (option 5) | REST (`/api/backtest`) |
| :----------- | -------------: | ---------------------: |
| Total return |      16.8397 % |         0.168397074... |
| CAGR         |       6.7666 % |         0.067665933... |
| Max drawdown |      15.0308 % |         0.150308097... |
| Trades       |             14 |                     14 |

The CLI prints percentages rounded to four decimals, and the REST response is the full-precision fraction.

## Reading a result

Compare `metrics` with `benchmark`, field by field:

- A strategy that earns less than buy and hold but with a much lower drawdown or volatility may still be useful. Compare risk-adjusted figures (`sharpe_ratio`, `sortino_ratio`), not just `total_return`.
- A high `num_trades` with a thin edge is fragile, because costs scale with turnover. Rerun with higher `transaction_cost_bps` to see how quickly the edge disappears.
- Judge a strategy on one instrument with caution. Run it across several, and on different date ranges.

## What the backtester does not do

- It tests **one instrument at a time**. There is no portfolio-level backtest.
- It trades at the **close**, with no intrabar prices, partial fills or market impact beyond the flat basis-point cost.
- Costs are **proportional only**. There is no fixed fee per trade and no bid-ask spread model.
- It uses **fixed parameters** over the whole history. There is no optimization and no walk-forward loop. Parameters you tune by looking at the full result are fitted to it, so the result is optimistic.
- It does not model borrowing costs or leverage. A weight is a fraction of equity.
- Dividends and splits are handled only through the adjusted prices in the data.

The Python package `ai_models` (in `code/ai_models`) offers a walk-forward alternative for model-driven strategies. Its backtester uses this same cost model, and a golden-file test keeps it identical to the C++ engine.
