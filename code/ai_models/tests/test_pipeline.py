import json
import warnings

import numpy as np
import pandas as pd
import pytest
from ai_models.config import CostModel, ModelConfig, WalkForwardConfig
from ai_models.exceptions import InsufficientDataError
from ai_models.pipeline import (
    fit_bundle,
    train_and_save,
    train_symbol,
    walk_forward_evaluate,
)
from ai_models.registry import ModelRegistry
from ai_models.signals import SignalPolicy
from ai_models.testing import synthetic_bars

WF = WalkForwardConfig(min_train=200, test_size=21, step=21)


@pytest.fixture(scope="module")
def report(bars):
    return walk_forward_evaluate(bars, "TEST", walk_forward=WF)


class TestFitBundle:
    def test_fits_all_four_models(self, fitted_bundle):
        assert fitted_bundle.direction.is_fitted
        assert fitted_bundle.volatility.is_fitted
        assert fitted_bundle.regime.is_fitted
        assert fitted_bundle.anomaly.is_fitted
        assert fitted_bundle.symbol == "TEST"

    def test_the_config_is_kept_with_the_models(self, bars):
        config = ModelConfig(direction_model="gradient_boosting", horizon=2)

        assert fit_bundle(bars, "X", config).config == config

    def test_too_little_data(self):
        with pytest.raises(InsufficientDataError):
            fit_bundle(synthetic_bars(n=80), "X")


class TestWalkForwardReport:
    def test_fold_count_follows_the_split_arithmetic(self, bars, report):
        rows = len(bars) - 63 - 1
        expected = int(np.ceil((rows - (WF.min_train + 1)) / WF.step))

        assert report.folds == expected

    def test_predictions_cover_each_out_of_sample_day_once(self, report):
        assert report.predictions.index.is_unique
        assert report.n_oos == len(report.predictions)
        assert report.predictions.index.is_monotonic_increasing

    def test_nothing_before_the_first_test_window_is_predicted(self, bars, report):
        first_row = 63 + WF.min_train + 1
        assert report.predictions.index[0] == bars.index[first_row]

    def test_dates_and_counts_are_reported(self, report):
        assert report.oos_start == report.predictions.index[0].strftime("%Y-%m-%d")
        assert report.oos_end == report.predictions.index[-1].strftime("%Y-%m-%d")
        assert report.classification["n"] == report.n_oos

    def test_probabilities_and_weights_are_valid(self, report):
        frame = report.predictions

        assert ((frame["prob_up"] > 0) & (frame["prob_up"] < 1)).all()
        assert frame["weight"].isin([0.0, 1.0]).all()
        assert (
            (frame["vol_target_weight"] >= 0) & (frame["vol_target_weight"] <= 1)
        ).all()

    def test_volatility_forecasts_are_compared_with_two_baselines(self, report):
        assert set(report.volatility) == {"har", "ewma", "historical"}
        assert all(v["n"] > 0 for v in report.volatility.values())

    def test_both_strategies_are_traded_over_the_same_window(self, report):
        assert report.backtest.dates == report.volatility_backtest.dates
        assert report.backtest.dates[0] == report.oos_start
        assert "ML Direction" in report.backtest.strategy
        assert "Volatility Target" in report.volatility_backtest.strategy

    def test_the_benchmark_is_buy_and_hold_over_the_window(self, bars, report):
        window = bars.loc[report.oos_start : report.backtest.dates[-1]]
        daily = window["close"].pct_change().dropna()
        daily.iloc[0] -= CostModel().rate  # buy-and-hold pays the entry cost once

        expected = float((1.0 + daily).prod() - 1.0)
        assert report.backtest.benchmark.total_return == pytest.approx(
            expected, rel=1e-9
        )
        assert report.backtest.benchmark.num_trades == 1

    def test_the_summary_is_strict_json(self, report):
        json.dumps(report.summary(), allow_nan=False)

    def test_the_backtest_serialises_like_the_engine_response(self, report):
        payload = report.backtest.to_dict()

        json.dumps(payload, allow_nan=False)
        assert len(payload["equity_curve"]) == len(report.backtest.dates)

    def test_is_deterministic(self, bars, report):
        again = walk_forward_evaluate(bars, "TEST", walk_forward=WF)

        pd.testing.assert_frame_equal(report.predictions, again.predictions)
        assert report.classification == again.classification


class TestNoLookAhead:
    def test_truncating_the_future_does_not_change_earlier_predictions(
        self, bars, report
    ):
        """Out-of-sample predictions must not depend on any later bar."""
        truncated = walk_forward_evaluate(bars.iloc[:450], "TEST", walk_forward=WF)

        common = truncated.predictions.index
        assert len(common) > 100
        pd.testing.assert_frame_equal(
            truncated.predictions[["prob_up", "forecast_vol", "weight"]],
            report.predictions.loc[common, ["prob_up", "forecast_vol", "weight"]],
        )

    def test_rewriting_the_future_does_not_change_earlier_predictions(
        self, bars, report
    ):
        altered = bars.copy()
        cut = 450
        rng = np.random.default_rng(5)
        for column in ["open", "high", "low", "close"]:
            altered.iloc[cut:, altered.columns.get_loc(column)] *= rng.uniform(
                0.7, 1.4, len(bars) - cut
            )
        altered["high"] = altered[["open", "high", "low", "close"]].max(axis=1)
        altered["low"] = altered[["open", "high", "low", "close"]].min(axis=1)

        redone = walk_forward_evaluate(altered, "TEST", walk_forward=WF)

        safe = report.predictions.index[report.predictions.index < bars.index[cut - 1]]
        pd.testing.assert_frame_equal(
            redone.predictions.loc[safe, ["prob_up", "forecast_vol"]],
            report.predictions.loc[safe, ["prob_up", "forecast_vol"]],
        )


class TestDetectingSignal:
    @staticmethod
    def reversal_bars(n=700, seed=3, strength=-0.5):
        """Returns that mean-revert strongly: yesterday's return predicts today's."""
        rng = np.random.default_rng(seed)
        returns = np.zeros(n)
        for i in range(1, n):
            returns[i] = strength * returns[i - 1] + rng.normal(0, 0.01)
        close = 100 * np.exp(np.cumsum(returns))
        bars = pd.DataFrame(
            {
                "open": close,
                "high": close * 1.002,
                "low": close * 0.998,
                "close": close,
                "volume": np.full(n, 1_000_000.0),
            },
            index=pd.bdate_range("2020-01-01", periods=n, name="date"),
        )
        return bars

    def test_a_real_predictable_pattern_is_found_out_of_sample(self):
        result = walk_forward_evaluate(self.reversal_bars(), "REV", walk_forward=WF)

        assert result.classification["auc"] > 0.60
        assert result.information_coefficient > 0.10
        assert result.classification["edge_vs_majority"] > 0.0

    def test_pure_noise_shows_no_edge(self):
        noise = self.reversal_bars(strength=0.0, seed=11)

        result = walk_forward_evaluate(noise, "NOISE", walk_forward=WF)

        assert 0.42 < result.classification["auc"] < 0.58
        assert abs(result.information_coefficient) < 0.15


class TestConfiguration:
    def test_costs_reduce_the_strategy_return(self, bars):
        free = walk_forward_evaluate(
            bars, "T", walk_forward=WF, cost=CostModel(0.0, 0.0)
        )
        costly = walk_forward_evaluate(
            bars, "T", walk_forward=WF, cost=CostModel(50.0, 50.0)
        )

        assert costly.backtest.metrics.total_return < free.backtest.metrics.total_return

    def test_a_lower_volatility_target_holds_less(self, bars):
        high = walk_forward_evaluate(bars, "T", walk_forward=WF, target_volatility=0.5)
        low = walk_forward_evaluate(bars, "T", walk_forward=WF, target_volatility=0.05)

        assert (
            low.predictions["vol_target_weight"].mean()
            < high.predictions["vol_target_weight"].mean()
        )
        assert "5%" in low.volatility_backtest.strategy

    def test_the_signal_policy_changes_the_weights(self, bars):
        strict = walk_forward_evaluate(
            bars, "T", walk_forward=WF, policy=SignalPolicy(long_threshold=0.9)
        )

        assert strict.predictions["weight"].mean() < 0.05

    def test_gradient_boosting_can_be_evaluated(self, bars):
        result = walk_forward_evaluate(
            bars,
            "T",
            ModelConfig(direction_model="gradient_boosting"),
            WalkForwardConfig(min_train=250, test_size=60, step=60),
        )

        assert "gradient_boosting" in result.backtest.strategy

    def test_too_little_data_for_a_single_fold(self):
        with pytest.raises(InsufficientDataError, match="too few"):
            # 250 bars leave 186 labelled rows, fewer than min_train + embargo = 201.
            walk_forward_evaluate(synthetic_bars(n=250), "T", walk_forward=WF)


class TestTraining:
    def test_train_symbol_returns_bundle_data_and_evaluation(self, bars):
        result = train_symbol(bars, "TEST", walk_forward=WF)

        assert result.bundle.direction.is_fitted
        assert result.data.rows == len(bars)
        assert result.data.start == bars.index[0].strftime("%Y-%m-%d")
        assert result.data.end == bars.index[-1].strftime("%Y-%m-%d")
        assert result.evaluation is not None

    def test_evaluation_can_be_skipped(self, bars):
        assert train_symbol(bars, "TEST", evaluate=False).evaluation is None

    def test_train_and_save_stores_the_summary(self, bars, tmp_path):
        registry = ModelRegistry(tmp_path)

        metadata, evaluation = train_and_save(registry, bars, "TEST", walk_forward=WF)

        assert registry.metadata("TEST") == metadata
        assert metadata.metrics["folds"] == evaluation.folds
        assert "strategy" in metadata.metrics
        assert registry.load("TEST").direction.is_fitted

    def test_train_and_save_without_evaluation_stores_no_metrics(self, bars, tmp_path):
        metadata, evaluation = train_and_save(
            ModelRegistry(tmp_path), bars, "TEST", evaluate=False
        )

        assert evaluation is None
        assert metadata.metrics == {}


def test_no_unexpected_warnings_during_evaluation(bars):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        walk_forward_evaluate(bars, "T", walk_forward=WF)
