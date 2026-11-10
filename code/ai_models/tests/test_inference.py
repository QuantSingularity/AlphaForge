import numpy as np
import pandas as pd
import pytest
from ai_models.exceptions import InsufficientDataError
from ai_models.inference import Prediction, Predictor
from ai_models.signals import SignalPolicy
from ai_models.testing import synthetic_bars


@pytest.fixture(scope="module")
def predictor(fitted_bundle):
    return Predictor(fitted_bundle)


class TestPredict:
    def test_returns_one_prediction_per_requested_row(self, predictor, bars):
        predictions = predictor.predict(bars, rows=5)

        assert len(predictions) == 5
        assert all(isinstance(p, Prediction) for p in predictions)
        assert predictions[-1].date == bars.index[-1].strftime("%Y-%m-%d")

    def test_values_are_in_range(self, predictor, bars):
        for p in predictor.predict(bars, rows=30):
            assert 0.0 < p.prob_up < 1.0
            assert 0.0 <= p.signal_weight <= 1.0
            assert p.forecast_vol is not None and p.forecast_vol > 0
            assert p.regime in {"bear", "sideways", "bull"}
            assert sum(p.regime_probabilities.values()) == pytest.approx(1.0)
            assert (
                max(p.regime_probabilities, key=p.regime_probabilities.get) == p.regime
            )
            assert 0.0 <= p.anomaly_score <= 1.0

    def test_a_prediction_never_uses_later_bars(self, predictor, bars):
        """Predicting from a truncated history gives the same answer as before."""
        full = predictor.predict(bars, rows=40)
        earlier = predictor.predict(bars.iloc[:-10], rows=1)[0]

        assert earlier.to_dict() == full[-11].to_dict()

    def test_only_the_last_prediction_is_explained(self, predictor, bars):
        predictions = predictor.predict(bars, rows=3, explain=True)

        assert predictions[0].contributions is None
        assert predictions[1].contributions is None
        assert predictions[2].contributions is not None

    def test_no_explanation_by_default(self, predictor, bars):
        assert predictor.predict(bars, rows=2)[-1].contributions is None

    def test_the_explanation_covers_every_feature(self, predictor, bars):
        from ai_models.features import FEATURE_COLUMNS

        explanation = predictor.latest(bars).contributions

        assert set(explanation) == set(FEATURE_COLUMNS)

    def test_latest_is_the_last_prediction_with_an_explanation(self, predictor, bars):
        latest = predictor.latest(bars)

        assert latest.date == bars.index[-1].strftime("%Y-%m-%d")
        assert latest.contributions

    def test_the_signal_policy_controls_the_weight(self, fitted_bundle, bars):
        always = Predictor(fitted_bundle, SignalPolicy(long_threshold=0.01)).latest(
            bars
        )
        never = Predictor(fitted_bundle, SignalPolicy(long_threshold=0.99)).latest(bars)

        assert always.signal_weight == 1.0
        assert never.signal_weight == 0.0

    def test_volatility_targeting_scales_the_weight_down(self, fitted_bundle, bars):
        policy = SignalPolicy(long_threshold=0.01, target_volatility=0.001)

        prediction = Predictor(fitted_bundle, policy).latest(bars)

        assert 0.0 < prediction.signal_weight < 0.5

    def test_too_few_bars(self, predictor):
        with pytest.raises(InsufficientDataError):
            predictor.predict(synthetic_bars(n=50))

    def test_to_dict_is_json_ready(self, predictor, bars):
        import json

        payload = predictor.latest(bars).to_dict()

        json.dumps(payload, allow_nan=False)
        assert set(payload) >= {
            "date",
            "prob_up",
            "signal_weight",
            "forecast_vol",
            "regime",
        }

    def test_predictions_are_deterministic(self, predictor, bars):
        first = predictor.predict(bars, rows=5)
        second = predictor.predict(bars, rows=5)

        assert [p.to_dict() for p in first] == [p.to_dict() for p in second]

    def test_probabilities_match_the_model_directly(
        self, predictor, bars, fitted_bundle
    ):
        from ai_models.features import latest_features

        direct = fitted_bundle.direction.predict_proba_up(latest_features(bars, 3))
        via_predictor = [p.prob_up for p in predictor.predict(bars, rows=3)]

        np.testing.assert_allclose(via_predictor, direct)
        assert isinstance(pd.Timestamp(predictor.latest(bars).date), pd.Timestamp)
