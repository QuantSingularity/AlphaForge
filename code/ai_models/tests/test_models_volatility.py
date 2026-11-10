import numpy as np
import pandas as pd
import pytest
from ai_models.config import ModelConfig
from ai_models.exceptions import DataError, InsufficientDataError
from ai_models.models import (
    VolatilityForecaster,
    ewma_volatility,
    historical_volatility,
    realized_volatility,
)
from ai_models.models.volatility import (
    HAR_COLUMNS,
    future_variance,
    har_features,
    log_returns,
)
from ai_models.testing import synthetic_bars


def returns_from(values):
    return pd.Series(values, index=pd.bdate_range("2020-01-01", periods=len(values)))


class TestHelpers:
    def test_har_features_are_mean_squared_percent_returns(self):
        returns = returns_from([0.01] * 30)

        features = har_features(returns)

        assert list(features.columns) == list(HAR_COLUMNS)
        assert features["rv_d"].iloc[-1] == pytest.approx(1.0)
        assert features["rv_w"].iloc[-1] == pytest.approx(1.0)
        assert features["rv_m"].iloc[-1] == pytest.approx(1.0)
        assert features["rv_w"].iloc[:4].isna().all()
        assert features["rv_m"].iloc[:20].isna().all()

    def test_future_variance_looks_forward_by_the_horizon(self):
        returns = returns_from([0.01, 0.02, 0.03, 0.04, 0.05])

        target = future_variance(returns, 2)

        assert target.iloc[0] == pytest.approx((4.0 + 9.0) / 2)
        assert target.iloc[-2:].isna().all()

    def test_realized_volatility_is_annualised(self):
        returns = returns_from([0.01] * 10)

        assert realized_volatility(returns, 5).iloc[0] == pytest.approx(
            0.01 * np.sqrt(252)
        )

    def test_ewma_is_undefined_during_warm_up(self):
        returns = returns_from(np.random.default_rng(0).normal(0, 0.01, 60))

        series = ewma_volatility(returns)

        assert series.iloc[:19].isna().all()
        assert series.iloc[19:].notna().all()

    def test_ewma_reacts_faster_than_a_long_window(self):
        returns = returns_from(np.concatenate([np.full(100, 0.001), np.full(5, 0.05)]))

        assert (
            ewma_volatility(returns).iloc[-1]
            > 3 * historical_volatility(returns, 100).iloc[-1] / 3
        )

    def test_historical_volatility_is_the_annualised_sample_std(self):
        rng = np.random.default_rng(1)
        returns = returns_from(rng.normal(0, 0.01, 300))

        expected = returns.iloc[-252:].std() * np.sqrt(252)
        assert historical_volatility(returns).iloc[-1] == pytest.approx(expected)


class TestForecaster:
    def test_forecast_is_a_positive_annualised_volatility(self, bars):
        forecast = VolatilityForecaster().fit(bars).forecast(bars)

        assert 0.01 < forecast < 2.0

    def test_coefficients_are_non_negative(self, bars):
        coefficients = VolatilityForecaster().fit(bars).coefficients

        assert all(coefficients[c] >= 0 for c in HAR_COLUMNS)
        assert "intercept" in coefficients

    def test_forecasts_track_realised_volatility(self, bars):
        model = VolatilityForecaster().fit(bars)
        frame = pd.DataFrame(
            {
                "forecast": model.forecast_series(bars),
                "realised": realized_volatility(log_returns(bars), 1),
            }
        ).dropna()

        assert frame["forecast"].corr(frame["realised"]) > 0.2

    def test_is_causal(self, bars):
        model = VolatilityForecaster().fit(bars)
        full = model.forecast_series(bars)
        truncated = model.forecast_series(bars.iloc[:300])

        pd.testing.assert_series_equal(truncated, full.iloc[:300], check_freq=False)

    def test_warm_up_rows_have_no_forecast(self, bars):
        series = VolatilityForecaster().fit(bars).forecast_series(bars)

        assert series.iloc[:20].isna().all()
        assert series.iloc[21:].notna().all()

    def test_a_calm_market_is_forecast_lower_than_a_turbulent_one(self):
        calm = synthetic_bars(n=400, seed=2, base_volatility=0.004)
        wild = synthetic_bars(n=400, seed=2, base_volatility=0.03)

        calm_vol = VolatilityForecaster().fit(calm).forecast(calm)
        wild_vol = VolatilityForecaster().fit(wild).forecast(wild)

        assert wild_vol > 3 * calm_vol

    def test_longer_horizons_are_supported(self, bars):
        model = VolatilityForecaster(ModelConfig(horizon=5)).fit(bars)

        assert model.forecast(bars) > 0

    def test_is_deterministic(self, bars):
        first = VolatilityForecaster().fit(bars).forecast(bars)
        second = VolatilityForecaster().fit(bars).forecast(bars)

        assert first == second

    def test_too_little_data(self):
        with pytest.raises(InsufficientDataError, match="at least"):
            VolatilityForecaster().fit(synthetic_bars(n=70))

    def test_using_the_model_before_fitting(self, bars):
        model = VolatilityForecaster()

        assert not model.is_fitted
        with pytest.raises(DataError, match="not been fitted"):
            model.forecast_series(bars)
        with pytest.raises(DataError, match="not been fitted"):
            _ = model.coefficients

    def test_too_few_bars_to_forecast(self, bars):
        model = VolatilityForecaster().fit(bars)

        with pytest.raises(InsufficientDataError, match="too few"):
            model.forecast(bars.iloc[:10])
