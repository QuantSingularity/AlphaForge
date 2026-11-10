import numpy as np
import pytest
from ai_models.config import WalkForwardConfig
from ai_models.evaluation import (
    classification_metrics,
    information_coefficient,
    volatility_metrics,
    walk_forward_splits,
)


class TestWalkForwardSplits:
    config = WalkForwardConfig(min_train=100, test_size=20, step=20)

    def test_first_fold_trains_on_min_train_rows(self):
        first = walk_forward_splits(300, self.config, horizon=1)[0]

        assert first.train_end == 100
        assert first.test_start == 101
        assert first.test_end == 121

    def test_folds_expand_the_training_window(self):
        splits = walk_forward_splits(300, self.config, horizon=1)

        assert [s.train_end for s in splits] == sorted({s.train_end for s in splits})
        assert splits[1].train_end - splits[0].train_end == 20

    def test_every_training_label_ends_before_the_test_window(self):
        for horizon in (1, 3, 10):
            for split in walk_forward_splits(400, self.config, horizon):
                # The label of the last training row reaches row train_end - 1 + h.
                assert split.train_end - 1 + horizon < split.test_start

    def test_test_windows_never_overlap(self):
        splits = walk_forward_splits(400, self.config, horizon=1)

        for earlier, later in zip(splits, splits[1:]):
            assert earlier.test_end <= later.test_start

    def test_every_row_after_the_first_window_is_tested_exactly_once(self):
        splits = walk_forward_splits(300, self.config, horizon=1)

        tested = np.concatenate([np.arange(s.test_start, s.test_end) for s in splits])
        assert tested.tolist() == list(range(101, 300))

    def test_the_final_fold_may_be_partial(self):
        splits = walk_forward_splits(145, self.config, horizon=1)

        assert splits[-1].test_end == 145
        assert splits[-1].test_end - splits[-1].test_start < 20

    def test_too_few_rows_give_no_folds(self):
        assert walk_forward_splits(101, self.config, horizon=1) == []

    def test_a_larger_embargo_widens_the_gap(self):
        config = WalkForwardConfig(min_train=100, test_size=20, step=20, embargo=5)

        first = walk_forward_splits(300, config, horizon=1)[0]

        assert first.test_start - first.train_end == 5

    def test_embargo_below_the_horizon_is_rejected(self):
        config = WalkForwardConfig(min_train=100, test_size=20, step=20, embargo=1)

        with pytest.raises(ValueError, match="embargo"):
            walk_forward_splits(300, config, horizon=5)

    def test_horizon_must_be_positive(self):
        with pytest.raises(ValueError, match="horizon"):
            walk_forward_splits(300, self.config, horizon=0)

    def test_a_step_larger_than_the_test_size_leaves_gaps(self):
        config = WalkForwardConfig(min_train=100, test_size=10, step=30)

        splits = walk_forward_splits(300, config, horizon=1)

        assert splits[1].test_start - splits[0].test_end == 20


class TestClassificationMetrics:
    def test_perfect_probabilities_score_perfectly(self):
        y = np.array([0, 1, 0, 1, 1])
        p = np.array([0.01, 0.99, 0.02, 0.98, 0.97])

        result = classification_metrics(y, p)

        assert result["accuracy"] == 1.0
        assert result["auc"] == 1.0
        assert result["balanced_accuracy"] == 1.0
        assert result["brier"] < 0.001

    def test_inverted_probabilities_score_terribly(self):
        y = np.array([0, 1, 0, 1])
        p = np.array([0.9, 0.1, 0.9, 0.1])

        result = classification_metrics(y, p)

        assert result["accuracy"] == 0.0
        assert result["auc"] == 0.0

    def test_edge_is_measured_against_the_majority_class(self):
        y = np.array([1, 1, 1, 1, 0])
        always_up = np.full(5, 0.9)

        result = classification_metrics(y, always_up)

        assert result["accuracy"] == pytest.approx(0.8)
        assert result["base_rate"] == pytest.approx(0.8)
        assert result["edge_vs_majority"] == pytest.approx(0.0)

    def test_class_balanced_metrics_are_undefined_with_a_single_class(self):
        result = classification_metrics(np.ones(5, dtype=int), np.full(5, 0.7))

        assert result["auc"] is None
        assert result["balanced_accuracy"] is None
        assert result["accuracy"] == 1.0

    def test_extreme_probabilities_do_not_produce_infinities(self):
        result = classification_metrics(np.array([0, 1]), np.array([1.0, 0.0]))

        assert np.isfinite(result["log_loss"])

    def test_the_sample_size_is_reported(self):
        assert (
            classification_metrics(np.array([0, 1, 1]), np.array([0.4, 0.6, 0.7]))["n"]
            == 3
        )

    def test_no_observations_raise(self):
        with pytest.raises(ValueError, match="no observations"):
            classification_metrics(np.array([]), np.array([]))

    def test_the_result_is_json_safe(self):
        import json

        result = classification_metrics(
            np.array([0, 1, 1, 0]), np.array([0.3, 0.6, 0.7, 0.4])
        )

        json.dumps(result, allow_nan=False)


class TestInformationCoefficient:
    def test_a_monotone_signal_has_a_coefficient_of_one(self):
        score = np.arange(10, dtype=float)

        assert information_coefficient(score, score**3) == pytest.approx(1.0)

    def test_an_inverted_signal_has_a_coefficient_of_minus_one(self):
        score = np.arange(10, dtype=float)

        assert information_coefficient(score, -score) == pytest.approx(-1.0)

    def test_uses_ranks_so_outliers_do_not_dominate(self):
        score = np.arange(10, dtype=float)
        realised = score.copy()
        realised[-1] = 1e9

        assert information_coefficient(score, realised) == pytest.approx(1.0)

    def test_missing_pairs_are_ignored(self):
        score = np.array([1.0, 2.0, np.nan, 4.0, 5.0])
        realised = np.array([1.0, 2.0, 3.0, np.nan, 5.0])

        assert information_coefficient(score, realised) == pytest.approx(1.0)

    @pytest.mark.parametrize(
        ("score", "realised"),
        [([1.0, 1.0, 1.0, 1.0], [1.0, 2.0, 3.0, 4.0]), ([1.0, 2.0], [1.0, 2.0])],
    )
    def test_undefined_cases_give_none(self, score, realised):
        assert information_coefficient(np.array(score), np.array(realised)) is None


class TestVolatilityMetrics:
    def test_a_perfect_forecast_has_no_error(self):
        realised = np.array([0.10, 0.20, 0.15])

        result = volatility_metrics(realised, realised)

        assert result["rmse"] == pytest.approx(0.0)
        assert result["mae"] == pytest.approx(0.0)
        assert result["qlike"] == pytest.approx(0.0)

    def test_errors_are_in_volatility_units(self):
        result = volatility_metrics(np.array([0.2, 0.2]), np.array([0.1, 0.3]))

        assert result["rmse"] == pytest.approx(0.1)
        assert result["mae"] == pytest.approx(0.1)

    def test_qlike_punishes_under_forecasting_more_than_over_forecasting(self):
        realised = np.array([0.20] * 4)

        under = volatility_metrics(np.full(4, 0.10), realised)["qlike"]
        over = volatility_metrics(np.full(4, 0.40), realised)["qlike"]

        assert under > over > 0

    def test_missing_and_non_positive_values_are_dropped(self):
        forecast = np.array([0.1, np.nan, 0.2, -0.1, 0.0])
        realised = np.array([0.1, 0.1, 0.2, 0.1, 0.1])

        assert volatility_metrics(forecast, realised)["n"] == 2

    def test_nothing_usable_gives_empty_metrics(self):
        result = volatility_metrics(np.array([np.nan]), np.array([np.nan]))

        assert result == {"n": 0, "rmse": None, "mae": None, "qlike": None}
