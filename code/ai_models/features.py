"""Causal feature engineering for daily OHLCV bars.

Every feature at row t is computed only from bars at or before t. This is what
makes walk-forward evaluation honest, and it is enforced by a test that
recomputes the features on truncated histories and requires identical values.

Labels are the only forward-looking quantity. They live in separate functions
(forward_returns, make_dataset) and are never mixed into the feature frame.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import TRADING_DAYS_PER_YEAR
from .data import REQUIRED_COLUMNS
from .exceptions import DataError, InsufficientDataError

FEATURE_COLUMNS: tuple[str, ...] = (
    "ret_1",
    "ret_5",
    "ret_10",
    "ret_21",
    "ret_63",
    "vol_5",
    "vol_10",
    "vol_21",
    "vol_ratio",
    "rsi_14",
    "macd_hist",
    "bb_pctb",
    "sma_dist_20",
    "sma_dist_50",
    "atr_pct",
    "range_pct",
    "gap",
    "close_loc",
    "volume_z",
    "drawdown_63",
)

# Rows at the start of a history for which at least one feature is undefined.
WARMUP_ROWS = 63

_RSI_PERIOD = 14
_ATR_PERIOD = 14
_BOLLINGER_WINDOW = 20
_MACD_FAST, _MACD_SLOW, _MACD_SIGNAL = 12, 26, 9


@dataclass(frozen=True)
class Dataset:
    """Aligned features, labels and realised forward returns."""

    features: pd.DataFrame
    target: pd.Series
    forward_return: pd.Series

    def __len__(self) -> int:
        return len(self.features)


def _require_bars(bars: pd.DataFrame) -> None:
    missing = [c for c in REQUIRED_COLUMNS if c not in bars.columns]
    if missing:
        raise DataError(f"bars are missing columns: {', '.join(missing)}")
    if not bars.index.is_monotonic_increasing:
        raise DataError("bars must be sorted by date ascending")


def _wilder(series: pd.Series, period: int) -> pd.Series:
    """Wilder's smoothing: an exponential average with alpha = 1 / period."""
    return series.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def _rsi(close: pd.Series, period: int = _RSI_PERIOD) -> pd.Series:
    change = close.diff()
    gain = _wilder(change.clip(lower=0.0), period)
    loss = _wilder((-change).clip(lower=0.0), period)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = gain / loss
        rsi = 100.0 - 100.0 / (1.0 + rs)
    # No losses at all means RSI 100, unless there were no gains either.
    rsi = rsi.where(loss != 0.0, 100.0)
    rsi = rsi.where(~((loss == 0.0) & (gain == 0.0)), 50.0)
    return rsi.where(gain.notna() & loss.notna())


def _true_range(bars: pd.DataFrame) -> pd.Series:
    previous_close = bars["close"].shift(1)
    ranges = pd.concat(
        [
            bars["high"] - bars["low"],
            (bars["high"] - previous_close).abs(),
            (bars["low"] - previous_close).abs(),
        ],
        axis=1,
    )
    # skipna=False keeps the first bar undefined rather than pretending it has
    # a one-sided range.
    return ranges.max(axis=1, skipna=False)


def compute_features(bars: pd.DataFrame) -> pd.DataFrame:
    """Compute the causal feature frame for a bars frame.

    The result has the same index as bars and the columns of FEATURE_COLUMNS. The
    first WARMUP_ROWS rows contain NaN while the rolling windows fill.
    """
    _require_bars(bars)
    close, high, low = bars["close"], bars["high"], bars["low"]
    open_, volume = bars["open"], bars["volume"]

    log_close = np.log(close)
    log_ret = log_close.diff()
    annualise = float(np.sqrt(TRADING_DAYS_PER_YEAR))

    out = pd.DataFrame(index=bars.index)
    out["ret_1"] = log_ret
    for window in (5, 10, 21, 63):
        out[f"ret_{window}"] = log_close - log_close.shift(window)
    for window in (5, 10, 21):
        out[f"vol_{window}"] = log_ret.rolling(window).std() * annualise
    with np.errstate(divide="ignore", invalid="ignore"):
        out["vol_ratio"] = out["vol_5"] / out["vol_21"]

    out["rsi_14"] = _rsi(close)

    ema_fast = close.ewm(span=_MACD_FAST, adjust=False, min_periods=_MACD_SLOW).mean()
    ema_slow = close.ewm(span=_MACD_SLOW, adjust=False, min_periods=_MACD_SLOW).mean()
    macd = ema_fast - ema_slow
    signal_line = macd.ewm(
        span=_MACD_SIGNAL, adjust=False, min_periods=_MACD_SIGNAL
    ).mean()
    out["macd_hist"] = (macd - signal_line) / close

    sma_20 = close.rolling(_BOLLINGER_WINDOW).mean()
    std_20 = close.rolling(_BOLLINGER_WINDOW).std(ddof=0)
    with np.errstate(divide="ignore", invalid="ignore"):
        pctb = 0.5 + (close - sma_20) / (4.0 * std_20)
    out["bb_pctb"] = pctb.where(std_20 > 0.0, 0.5).where(sma_20.notna())
    out["sma_dist_20"] = close / sma_20 - 1.0
    out["sma_dist_50"] = close / close.rolling(50).mean() - 1.0

    out["atr_pct"] = _wilder(_true_range(bars), _ATR_PERIOD) / close
    out["range_pct"] = (high - low) / close
    out["gap"] = open_ / close.shift(1) - 1.0

    spread = high - low
    with np.errstate(divide="ignore", invalid="ignore"):
        location = ((close - low) - (high - close)) / spread
    out["close_loc"] = location.where(spread > 0.0, 0.0)

    log_volume = np.log1p(volume)
    volume_mean = log_volume.rolling(20).mean()
    volume_std = log_volume.rolling(20).std()
    with np.errstate(divide="ignore", invalid="ignore"):
        zscore = (log_volume - volume_mean) / volume_std
    out["volume_z"] = zscore.where(volume_std > 0.0, 0.0).where(volume_mean.notna())

    out["drawdown_63"] = close / close.rolling(63).max() - 1.0

    out = out.replace([np.inf, -np.inf], np.nan)
    return out[list(FEATURE_COLUMNS)]


def forward_returns(bars: pd.DataFrame, horizon: int = 1) -> pd.Series:
    """Simple return from each close to the close `horizon` bars later.

    The last `horizon` rows are NaN: their outcome is not known yet.
    """
    if horizon < 1:
        raise ValueError("horizon must be at least 1")
    _require_bars(bars)
    close = bars["close"]
    return (close.shift(-horizon) / close - 1.0).rename("forward_return")


def make_dataset(bars: pd.DataFrame, horizon: int = 1) -> Dataset:
    """Build the supervised dataset: features, up/down label and forward return.

    Rows with an undefined feature (warm-up) or an unknown outcome (the final
    `horizon` rows) are dropped. The label is 1 when the forward return is
    positive.
    """
    features = compute_features(bars)
    outcome = forward_returns(bars, horizon)
    frame = features.join(outcome).dropna()
    if frame.empty:
        raise InsufficientDataError(
            f"{len(bars)} bars are not enough to build a dataset "
            f"(warm-up {WARMUP_ROWS} rows plus a horizon of {horizon})"
        )
    realised = frame["forward_return"]
    target = (realised > 0.0).astype(int).rename("up")
    return Dataset(
        features=frame[list(FEATURE_COLUMNS)],
        target=target,
        forward_return=realised,
    )


def latest_features(bars: pd.DataFrame, rows: int = 1) -> pd.DataFrame:
    """The last `rows` complete feature rows, for inference (no labels needed)."""
    if rows < 1:
        raise ValueError("rows must be at least 1")
    complete = compute_features(bars).dropna()
    if len(complete) < rows:
        raise InsufficientDataError(
            f"need {WARMUP_ROWS + rows} bars to produce {rows} feature row(s), "
            f"got {len(bars)}"
        )
    return complete.iloc[-rows:]
