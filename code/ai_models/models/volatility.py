"""Volatility forecasting.

The forecaster is a HAR-style regression (Corsi, 2009): the next period's
variance is explained by the squared returns of the last day, week and month.
It is compared in evaluation against two simple baselines, an EWMA estimate
(RiskMetrics, lambda 0.94) and the long-run average.

Returns are handled in percent inside the regression, which keeps the variances
near 1 instead of 1e-4 and the ridge penalty meaningful.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge

from ..config import TRADING_DAYS_PER_YEAR, ModelConfig
from ..exceptions import DataError, InsufficientDataError

HAR_COLUMNS = ("rv_d", "rv_w", "rv_m")
MIN_TRAIN_ROWS = 60
_PERCENT = 100.0
_VARIANCE_FLOOR = 1e-6
_EWMA_LAMBDA = 0.94


def log_returns(bars: pd.DataFrame) -> pd.Series:
    return np.log(bars["close"]).diff().rename("log_return")


def har_features(returns: pd.Series) -> pd.DataFrame:
    """Daily, weekly and monthly mean squared return (percent squared)."""
    squared = (returns * _PERCENT) ** 2
    return pd.DataFrame(
        {
            "rv_d": squared,
            "rv_w": squared.rolling(5).mean(),
            "rv_m": squared.rolling(21).mean(),
        }
    )


def future_variance(returns: pd.Series, horizon: int) -> pd.Series:
    """Mean squared return over the next `horizon` days (percent squared)."""
    squared = (returns * _PERCENT) ** 2
    return squared.rolling(horizon).mean().shift(-horizon)


def _annualise(variance_pct2: np.ndarray | pd.Series) -> np.ndarray | pd.Series:
    return np.sqrt(variance_pct2 * TRADING_DAYS_PER_YEAR) / _PERCENT


def realized_volatility(returns: pd.Series, horizon: int) -> pd.Series:
    """Annualised volatility actually realised over the next `horizon` days."""
    return _annualise(future_variance(returns, horizon)).rename("realized_vol")


def ewma_volatility(returns: pd.Series, decay: float = _EWMA_LAMBDA) -> pd.Series:
    """RiskMetrics EWMA volatility, annualised, using returns up to each date."""
    squared = (returns * _PERCENT) ** 2
    variance = squared.ewm(alpha=1.0 - decay, adjust=False, min_periods=20).mean()
    return _annualise(variance).rename("ewma_vol")


def historical_volatility(returns: pd.Series, window: int = 252) -> pd.Series:
    """Rolling sample volatility, annualised, using returns up to each date."""
    return (
        returns.rolling(window, min_periods=21).std() * np.sqrt(TRADING_DAYS_PER_YEAR)
    ).rename("hist_vol")


class VolatilityForecaster:
    """Forecasts annualised volatility over the next `horizon` days."""

    def __init__(self, config: ModelConfig | None = None) -> None:
        self.config = config or ModelConfig()
        self._model: Ridge | None = None

    def fit(self, bars: pd.DataFrame) -> VolatilityForecaster:
        returns = log_returns(bars)
        frame = har_features(returns).join(
            future_variance(returns, self.config.horizon).rename("target")
        )
        frame = frame.dropna()
        if len(frame) < MIN_TRAIN_ROWS:
            raise InsufficientDataError(
                f"need at least {MIN_TRAIN_ROWS} rows to fit the volatility model, "
                f"got {len(frame)}"
            )
        # Non-negative coefficients keep the forecast a weighted average of the
        # recent variances, which is the economic content of the HAR model.
        model = Ridge(alpha=self.config.volatility_alpha, positive=True)
        model.fit(frame[list(HAR_COLUMNS)].to_numpy(), frame["target"].to_numpy())
        self._model = model
        return self

    @property
    def is_fitted(self) -> bool:
        return self._model is not None

    @property
    def coefficients(self) -> dict[str, float]:
        if self._model is None:
            raise DataError("the volatility model has not been fitted")
        values = {name: float(c) for name, c in zip(HAR_COLUMNS, self._model.coef_)}
        values["intercept"] = float(self._model.intercept_)
        return values

    def forecast_series(self, bars: pd.DataFrame) -> pd.Series:
        """Forecast for every date, using only returns up to that date."""
        if self._model is None:
            raise DataError("the volatility model has not been fitted")
        features = har_features(log_returns(bars))
        valid = features.notna().all(axis=1)
        forecast = pd.Series(np.nan, index=bars.index, name="forecast_vol")
        if valid.any():
            variance = self._model.predict(features.loc[valid].to_numpy())
            forecast.loc[valid] = _annualise(np.maximum(variance, _VARIANCE_FLOOR))
        return forecast

    def forecast(self, bars: pd.DataFrame) -> float:
        """Annualised volatility forecast for the days after the last bar."""
        series = self.forecast_series(bars).dropna()
        if series.empty:
            raise InsufficientDataError("too few bars to forecast volatility")
        return float(series.iloc[-1])
