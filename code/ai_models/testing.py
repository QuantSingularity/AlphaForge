"""Deterministic synthetic OHLCV data for tests and demonstrations.

The runtime (training, the service, the CLI) only ever reads real data files.
This module exists so the test suite can run anywhere without market data and
without network access.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .data import REQUIRED_COLUMNS


def synthetic_bars(
    n: int = 600,
    seed: int = 0,
    start_price: float = 100.0,
    daily_drift: float = 0.0003,
    base_volatility: float = 0.01,
    start: str = "2020-01-01",
) -> pd.DataFrame:
    """Generate valid daily bars with volatility clustering, gaps and volume.

    Daily volatility follows a persistent GARCH-like recursion, so the series has
    the clustering real markets show. The result passes validate_bars().
    """
    if n < 2:
        raise ValueError("n must be at least 2")
    rng = np.random.default_rng(seed)

    sigma = np.empty(n)
    shocks = rng.standard_normal(n)
    returns = np.empty(n)
    sigma[0] = base_volatility
    omega, alpha, beta = 0.05 * base_volatility**2, 0.08, 0.90
    for i in range(n):
        if i > 0:
            sigma[i] = np.sqrt(
                omega + alpha * returns[i - 1] ** 2 + beta * sigma[i - 1] ** 2
            )
        returns[i] = daily_drift + sigma[i] * shocks[i]

    close = start_price * np.exp(np.cumsum(returns))
    gap = rng.normal(0.0, sigma * 0.3)
    open_ = np.empty(n)
    open_[0] = start_price
    open_[1:] = close[:-1] * np.exp(gap[1:])
    spread = np.abs(rng.normal(0.0, sigma * 0.6)) + 0.0005
    high = np.maximum(open_, close) * (1.0 + spread)
    low = np.minimum(open_, close) * (1.0 - spread)
    volume = np.round(
        1_000_000 * np.exp(rng.normal(0.0, 0.25, n) + 8.0 * np.abs(returns))
    )

    index = pd.bdate_range(start=start, periods=n, name="date")
    frame = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=index,
    )
    return frame[list(REQUIRED_COLUMNS)].astype(float)


def write_csv(bars: pd.DataFrame, path: str | Path) -> Path:
    """Write bars in the exact format the engine and read_bars() expect."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    out = bars.copy()
    out.index = out.index.strftime("%Y-%m-%d")
    out.index.name = "date"
    out["volume"] = out["volume"].astype("int64")
    out.round(4).to_csv(path)
    return path
