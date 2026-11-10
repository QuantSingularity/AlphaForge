import math

import numpy as np
import pytest
from ai_models import metrics
from ai_models.metrics import PerformanceMetrics, summarize


class TestSampleStd:
    def test_uses_the_n_minus_one_denominator(self):
        assert metrics.sample_std([1.0, 2.0, 3.0, 4.0]) == pytest.approx(
            math.sqrt(5.0 / 3.0)
        )

    @pytest.mark.parametrize("values", [[], [5.0]])
    def test_fewer_than_two_values_give_zero(self, values):
        assert metrics.sample_std(values) == 0.0


class TestVolatility:
    def test_annualises_with_the_square_root_of_the_periods(self):
        returns = np.array([0.01, -0.01, 0.01, -0.01])

        expected = metrics.sample_std(returns) * math.sqrt(252)
        assert metrics.annualized_volatility(returns) == pytest.approx(expected)

    def test_periods_per_year_is_configurable(self):
        returns = np.array([0.01, -0.02, 0.03])

        assert metrics.annualized_volatility(returns, 12) == pytest.approx(
            metrics.sample_std(returns) * math.sqrt(12)
        )


class TestSharpe:
    def test_matches_the_definition(self):
        returns = np.array([0.01, 0.02, -0.01, 0.005])

        excess = returns.mean() - 0.02 / 252
        expected = excess / np.std(returns, ddof=1) * math.sqrt(252)
        assert metrics.sharpe_ratio(returns) == pytest.approx(expected)

    def test_zero_variance_gives_zero(self):
        assert metrics.sharpe_ratio(np.full(10, 0.01)) == 0.0

    def test_a_single_observation_gives_zero(self):
        assert metrics.sharpe_ratio(np.array([0.05])) == 0.0

    def test_risk_free_rate_lowers_the_ratio(self):
        returns = np.array([0.01, 0.02, -0.01, 0.005])

        assert metrics.sharpe_ratio(returns, 0.0) > metrics.sharpe_ratio(returns, 0.05)


class TestDownsideAndSortino:
    def test_downside_deviation_is_the_rms_shortfall_below_the_target(self):
        returns = np.array([-0.02, 0.01, -0.04, 0.03])

        expected = math.sqrt((0.02**2 + 0.04**2) / 2)
        assert metrics.downside_deviation(returns, 0.0) == pytest.approx(expected)

    def test_no_shortfall_gives_zero_deviation(self):
        assert metrics.downside_deviation(np.array([0.01, 0.02]), 0.0) == 0.0

    def test_sortino_divides_by_the_downside_deviation(self):
        returns = np.array([-0.02, 0.01, -0.04, 0.03])
        target = 0.02 / 252

        expected = (returns.mean() - target) / metrics.downside_deviation(
            returns, target
        )
        assert metrics.sortino_ratio(returns) == pytest.approx(
            expected * math.sqrt(252)
        )

    def test_sortino_is_zero_without_downside_or_data(self):
        assert metrics.sortino_ratio(np.array([0.01, 0.02, 0.03])) == 0.0
        assert metrics.sortino_ratio(np.array([])) == 0.0


class TestMaxDrawdown:
    def test_measures_the_worst_peak_to_trough_fall(self):
        equity = np.array([100.0, 120.0, 90.0, 110.0, 80.0, 130.0])

        assert metrics.max_drawdown(equity) == pytest.approx((120 - 80) / 120)

    def test_a_rising_curve_has_no_drawdown(self):
        assert metrics.max_drawdown(np.array([1.0, 2.0, 3.0])) == 0.0

    def test_empty_curve_gives_zero(self):
        assert metrics.max_drawdown(np.array([])) == 0.0


class TestSummarize:
    def test_a_known_curve(self):
        equity = np.array([100.0, 110.0, 99.0, 108.9])
        returns = np.array([0.10, -0.10, 0.10])

        result = summarize(equity, returns, trades=2)

        assert result.total_return == pytest.approx(0.089)
        assert result.max_drawdown == pytest.approx(0.10)
        assert result.win_rate == pytest.approx(2 / 3)
        assert result.profit_factor == pytest.approx(0.20 / 0.10)
        assert result.num_trades == 2
        years = 3 / 252
        assert result.cagr == pytest.approx(1.089 ** (1 / years) - 1)

    def test_flat_days_do_not_count_towards_win_rate(self):
        equity = np.array([100.0, 100.0, 101.0, 101.0])
        returns = np.array([0.0, 0.01, 0.0])

        assert summarize(equity, returns, 1).win_rate == 1.0

    def test_profit_factor_is_capped_without_losses(self):
        equity = np.array([100.0, 101.0, 102.0])
        returns = np.array([0.01, 0.0099])

        assert summarize(equity, returns, 1).profit_factor == metrics.PROFIT_FACTOR_CAP

    def test_profit_factor_is_capped_when_losses_are_negligible(self):
        equity = np.array([100.0, 110.0, 109.99999])
        returns = np.array([0.10, -1e-7])

        assert summarize(equity, returns, 1).profit_factor == metrics.PROFIT_FACTOR_CAP

    def test_no_activity_gives_zero_profit_factor_and_win_rate(self):
        equity = np.array([100.0, 100.0, 100.0])

        result = summarize(equity, np.zeros(2), 0)

        assert (result.profit_factor, result.win_rate) == (0.0, 0.0)

    def test_too_short_a_curve_gives_empty_metrics(self):
        result = summarize(np.array([100.0]), np.array([]), 3)

        assert result == PerformanceMetrics(num_trades=3)

    def test_to_dict_has_the_engine_field_names(self):
        assert set(PerformanceMetrics().to_dict()) == {
            "total_return",
            "cagr",
            "annualized_volatility",
            "sharpe_ratio",
            "sortino_ratio",
            "max_drawdown",
            "win_rate",
            "profit_factor",
            "num_trades",
        }
