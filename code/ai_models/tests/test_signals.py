import numpy as np
import pytest
from ai_models.signals import (
    SignalPolicy,
    probability_to_weight,
    volatility_target_weight,
)


class TestThresholdMode:
    def test_goes_long_at_or_above_the_threshold(self):
        weights = probability_to_weight([0.50, 0.549, 0.55, 0.90], SignalPolicy())

        np.testing.assert_array_equal(weights, [0.0, 0.0, 1.0, 1.0])

    def test_threshold_is_configurable(self):
        weights = probability_to_weight([0.52, 0.60], SignalPolicy(long_threshold=0.6))

        np.testing.assert_array_equal(weights, [0.0, 1.0])

    def test_a_scalar_gives_a_one_element_array(self):
        assert probability_to_weight(0.7).tolist() == [1.0]

    def test_missing_probabilities_mean_no_position(self):
        weights = probability_to_weight([np.nan, 0.9, np.inf], SignalPolicy())

        np.testing.assert_array_equal(weights, [0.0, 1.0, 0.0])


class TestProportionalMode:
    policy = SignalPolicy(mode="proportional", long_threshold=0.55, full_threshold=0.65)

    def test_ramps_linearly_between_the_thresholds(self):
        weights = probability_to_weight([0.50, 0.55, 0.60, 0.65, 0.80], self.policy)

        np.testing.assert_allclose(weights, [0.0, 0.0, 0.5, 1.0, 1.0])

    def test_max_weight_caps_the_position(self):
        capped = SignalPolicy(
            mode="proportional",
            long_threshold=0.55,
            full_threshold=0.65,
            max_weight=0.5,
        )

        assert probability_to_weight(0.9, capped).tolist() == [0.5]


class TestVolatilityScaling:
    def test_scales_down_when_forecast_volatility_is_above_target(self):
        policy = SignalPolicy(target_volatility=0.10)

        weights = probability_to_weight([0.9, 0.9], policy, [0.20, 0.05])

        np.testing.assert_allclose(weights, [0.5, 1.0])

    def test_never_scales_up_above_the_signal(self):
        policy = SignalPolicy(target_volatility=0.50)

        assert probability_to_weight(0.9, policy, 0.10).tolist() == [1.0]

    def test_an_unusable_forecast_leaves_the_weight_unscaled(self):
        policy = SignalPolicy(target_volatility=0.10)

        weights = probability_to_weight([0.9, 0.9, 0.9], policy, [np.nan, 0.0, -1.0])

        np.testing.assert_array_equal(weights, [1.0, 1.0, 1.0])

    def test_volatility_is_ignored_without_a_target(self):
        assert probability_to_weight(0.9, SignalPolicy(), 5.0).tolist() == [1.0]


class TestPolicyValidation:
    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"mode": "kelly"}, "mode"),
            ({"long_threshold": 0.0}, "long_threshold"),
            ({"long_threshold": 1.0}, "long_threshold"),
            ({"mode": "proportional", "full_threshold": 0.5}, "full_threshold"),
            ({"target_volatility": 0.0}, "target_volatility"),
            ({"max_weight": 0.0}, "max_weight"),
            ({"max_weight": 1.5}, "max_weight"),
        ],
    )
    def test_invalid_policies_are_rejected(self, kwargs, message):
        with pytest.raises(ValueError, match=message):
            SignalPolicy(**kwargs)


class TestVolatilityTargetWeight:
    def test_is_target_over_forecast_capped_at_one(self):
        weights = volatility_target_weight([0.06, 0.12, 0.24, 0.48], 0.12)

        np.testing.assert_allclose(weights, [1.0, 1.0, 0.5, 0.25])

    def test_missing_or_non_positive_forecasts_give_no_position(self):
        weights = volatility_target_weight([np.nan, 0.0, -0.1, np.inf])

        np.testing.assert_array_equal(weights, [0.0, 0.0, 0.0, 0.0])

    def test_max_weight_applies(self):
        assert volatility_target_weight(0.01, 0.12, max_weight=0.8).tolist() == [0.8]

    @pytest.mark.parametrize("target", [0.0, -0.1])
    def test_target_must_be_positive(self, target):
        with pytest.raises(ValueError, match="target_volatility"):
            volatility_target_weight(0.2, target)

    def test_max_weight_must_be_valid(self):
        with pytest.raises(ValueError, match="max_weight"):
            volatility_target_weight(0.2, 0.1, max_weight=2.0)
