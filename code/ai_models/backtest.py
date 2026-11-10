"""Backtesting of position weights with the C++ engine's cost model.

The convention is the engine's: the weight stored for bar i-1 is decided with
information available at that bar's close and earns the return from bar i-1 to
bar i. Turnover is charged (transaction cost plus slippage) in the bar where the
position changes. Weights are long-only fractions of equity in [0, 1].
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from .config import INITIAL_CAPITAL, RISK_FREE_RATE, TRADING_DAYS_PER_YEAR, CostModel
from .metrics import PerformanceMetrics, summarize

_TRADE_EPSILON = 1e-9


@dataclass(frozen=True)
class BacktestResult:
    """Outcome of a backtest, shaped like the engine's /api/backtest response."""

    strategy: str
    symbol: str
    dates: tuple[str, ...]
    equity: np.ndarray
    weights: np.ndarray
    returns: np.ndarray
    metrics: PerformanceMetrics
    benchmark: PerformanceMetrics

    @property
    def held_weights(self) -> np.ndarray:
        """The weight actually held during each bar.

        The signal stored for bar i-1 is held during bar i, so this is the signal
        series shifted by one. The first bar reports its own signal, exactly as the
        engine does.
        """
        if self.weights.size == 0:
            return self.weights.copy()
        return np.concatenate(([self.weights[0]], self.weights[:-1]))

    def to_dict(self) -> dict[str, Any]:
        curve = [
            {
                "date": date,
                "equity": float(equity),
                "target_weight": float(weight),
            }
            for date, equity, weight in zip(self.dates, self.equity, self.held_weights)
        ]
        return {
            "strategy": self.strategy,
            "symbol": self.symbol,
            "equity_curve": curve,
            "metrics": self.metrics.to_dict(),
            "benchmark": self.benchmark.to_dict(),
        }


def _validate_weights(weights: np.ndarray, closes: np.ndarray) -> None:
    if weights.shape != closes.shape or weights.ndim != 1:
        raise ValueError(
            "weights and closes must be one-dimensional and equal in length"
        )
    if not np.isfinite(weights).all():
        raise ValueError("weights must be finite")
    if (weights < 0.0).any() or (weights > 1.0).any():
        raise ValueError("weights must lie in [0, 1] (long-only, no leverage)")
    if not np.isfinite(closes).all() or (closes <= 0).any():
        raise ValueError("closes must be finite and positive")


def simulate(
    weights: np.ndarray,
    closes: np.ndarray,
    cost: CostModel | None = None,
    initial_capital: float = INITIAL_CAPITAL,
) -> tuple[np.ndarray, int]:
    """Equity curve and trade count for a weight series (Backtester::simulate).

    Returns an array with one equity value per bar (the first bar is the initial
    capital) and the number of bars in which the position changed.
    """
    cost = cost or CostModel()
    weights = np.asarray(weights, dtype=float)
    closes = np.asarray(closes, dtype=float)
    _validate_weights(weights, closes)
    if closes.size < 2:
        return np.array([initial_capital] * closes.size, dtype=float), 0

    held = weights[:-1]
    previous = np.concatenate(([0.0], held[:-1]))
    turnover = np.abs(held - previous)
    asset_returns = closes[1:] / closes[:-1] - 1.0
    period_returns = held * asset_returns - turnover * cost.rate

    equity = np.empty(closes.size, dtype=float)
    equity[0] = initial_capital
    equity[1:] = initial_capital * np.cumprod(1.0 + period_returns)
    trades = int(np.count_nonzero(turnover > _TRADE_EPSILON))
    return equity, trades


def _period_returns(equity: np.ndarray) -> np.ndarray:
    if equity.size < 2:
        return np.empty(0, dtype=float)
    previous = equity[:-1]
    with np.errstate(divide="ignore", invalid="ignore"):
        returns = np.where(previous != 0.0, equity[1:] / previous - 1.0, 0.0)
    return returns


def run_backtest(
    strategy: str,
    symbol: str,
    dates: list[str] | tuple[str, ...],
    closes: np.ndarray,
    weights: np.ndarray,
    cost: CostModel | None = None,
    initial_capital: float = INITIAL_CAPITAL,
    risk_free_rate: float = RISK_FREE_RATE,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> BacktestResult:
    """Backtest a weight series and a buy-and-hold benchmark over the same window."""
    closes = np.asarray(closes, dtype=float)
    weights = np.asarray(weights, dtype=float)
    if len(dates) != closes.size:
        raise ValueError("dates and closes must have the same length")

    equity, trades = simulate(weights, closes, cost, initial_capital)
    returns = _period_returns(equity)
    metrics = summarize(equity, returns, trades, risk_free_rate, periods_per_year)

    hold_equity, hold_trades = simulate(
        np.ones_like(closes), closes, cost, initial_capital
    )
    hold_returns = _period_returns(hold_equity)
    benchmark = summarize(
        hold_equity, hold_returns, hold_trades, risk_free_rate, periods_per_year
    )

    return BacktestResult(
        strategy=strategy,
        symbol=symbol,
        dates=tuple(dates),
        equity=equity,
        weights=weights,
        returns=returns,
        metrics=metrics,
        benchmark=benchmark,
    )
