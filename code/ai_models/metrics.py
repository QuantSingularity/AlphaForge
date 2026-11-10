"""Performance and risk metrics.

Each function reproduces the corresponding C++ definition (RiskEngine.cpp and
Backtester.cpp) including its edge cases, so numbers computed here can be
compared one to one with the engine's.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from .config import RISK_FREE_RATE, TRADING_DAYS_PER_YEAR

PROFIT_FACTOR_CAP = 99.99
_ACTIVE_RETURN_EPSILON = 1e-12


def sample_std(values: np.ndarray) -> float:
    """Sample standard deviation (n - 1 denominator); 0 for fewer than 2 values."""
    values = np.asarray(values, dtype=float)
    if values.size < 2:
        return 0.0
    # Identical values have no dispersion. Rounding in the mean would otherwise
    # leave a residue near 1e-18, which the ratios below would divide by.
    if np.ptp(values) == 0.0:
        return 0.0
    return float(np.std(values, ddof=1))


def annualized_volatility(
    returns: np.ndarray, periods_per_year: int = TRADING_DAYS_PER_YEAR
) -> float:
    return sample_std(returns) * float(np.sqrt(periods_per_year))


def sharpe_ratio(
    returns: np.ndarray,
    risk_free_rate: float = RISK_FREE_RATE,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> float:
    returns = np.asarray(returns, dtype=float)
    if returns.size < 2:
        return 0.0
    sd = sample_std(returns)
    if sd == 0.0:
        return 0.0
    excess = float(returns.mean()) - risk_free_rate / periods_per_year
    return excess / sd * float(np.sqrt(periods_per_year))


def downside_deviation(returns: np.ndarray, minimum_acceptable: float) -> float:
    """Root mean square of the shortfalls, over the observations below the target."""
    returns = np.asarray(returns, dtype=float)
    below = returns[returns < minimum_acceptable]
    if below.size == 0:
        return 0.0
    return float(np.sqrt(np.sum((below - minimum_acceptable) ** 2) / below.size))


def sortino_ratio(
    returns: np.ndarray,
    risk_free_rate: float = RISK_FREE_RATE,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> float:
    returns = np.asarray(returns, dtype=float)
    if returns.size == 0:
        return 0.0
    target = risk_free_rate / periods_per_year
    deviation = downside_deviation(returns, target)
    if deviation == 0.0:
        return 0.0
    return (
        (float(returns.mean()) - target) / deviation * float(np.sqrt(periods_per_year))
    )


def max_drawdown(equity: np.ndarray) -> float:
    """Largest peak-to-trough fall of an equity curve, as a fraction of the peak."""
    equity = np.asarray(equity, dtype=float)
    if equity.size == 0:
        return 0.0
    peaks = np.maximum.accumulate(equity)
    with np.errstate(divide="ignore", invalid="ignore"):
        drawdowns = np.where(peaks > 0.0, (peaks - equity) / peaks, 0.0)
    return float(drawdowns.max())


@dataclass(frozen=True)
class PerformanceMetrics:
    """The same fields the C++ backtester reports for a strategy."""

    total_return: float = 0.0
    cagr: float = 0.0
    annualized_volatility: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    num_trades: int = 0

    def to_dict(self) -> dict[str, float | int]:
        return asdict(self)


def summarize(
    equity: np.ndarray,
    returns: np.ndarray,
    trades: int,
    risk_free_rate: float = RISK_FREE_RATE,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> PerformanceMetrics:
    """Summarise an equity curve and its period returns (Backtester::summarize)."""
    equity = np.asarray(equity, dtype=float)
    returns = np.asarray(returns, dtype=float)
    if equity.size < 2:
        return PerformanceMetrics(num_trades=trades)

    start, end = float(equity[0]), float(equity[-1])
    total_return = end / start - 1.0 if start != 0.0 else 0.0
    years = returns.size / periods_per_year
    cagr = 0.0
    if years > 0.0 and start > 0.0 and end > 0.0:
        cagr = (end / start) ** (1.0 / years) - 1.0

    active = returns[np.abs(returns) > _ACTIVE_RETURN_EPSILON]
    wins = active[active > 0.0]
    losses = active[active < 0.0]
    win_rate = wins.size / active.size if active.size else 0.0
    gross_gain = float(wins.sum())
    gross_loss = float(-losses.sum())
    profit_factor = 0.0
    if gross_loss > 0.0:
        profit_factor = min(PROFIT_FACTOR_CAP, gross_gain / gross_loss)
    elif gross_gain > 0.0:
        profit_factor = PROFIT_FACTOR_CAP

    return PerformanceMetrics(
        total_return=total_return,
        cagr=cagr,
        annualized_volatility=annualized_volatility(returns, periods_per_year),
        sharpe_ratio=sharpe_ratio(returns, risk_free_rate, periods_per_year),
        sortino_ratio=sortino_ratio(returns, risk_free_rate, periods_per_year),
        max_drawdown=max_drawdown(equity),
        win_rate=win_rate,
        profit_factor=profit_factor,
        num_trades=trades,
    )
