import numpy as np
import pandas as pd
import pytest
from ai_models.config import ModelConfig
from ai_models.exceptions import DataError, InsufficientDataError
from ai_models.features import compute_features
from ai_models.models import (
    ANOMALY_FEATURES,
    REGIME_FEATURES,
    AnomalyDetector,
    RegimeDetector,
)


@pytest.fixture(scope="module")
def features(bars):
    return compute_features(bars).dropna()


def three_regimes(n_per=120, seed=0):
    """Three well separated clusters of the regime state variables."""
    rng = np.random.default_rng(seed)
    centres = {
        "bear": (-0.12, 0.45, -0.15),
        "sideways": (0.00, 0.15, -0.03),
        "bull": (0.10, 0.10, -0.01),
    }
    frames, truth = [], []
    for name, centre in centres.items():
        noise = rng.normal(0, [0.02, 0.02, 0.01], (n_per, 3))
        frames.append(
            pd.DataFrame(np.array(centre) + noise, columns=list(REGIME_FEATURES))
        )
        truth += [name] * n_per
    return pd.concat(frames, ignore_index=True), np.array(truth)


class TestRegimeDetector:
    def test_recovers_well_separated_regimes(self):
        frame, truth = three_regimes()
        model = RegimeDetector().fit(frame)

        predicted = np.array(model.predict(frame))

        assert (predicted == truth).mean() > 0.97

    def test_labels_are_ordered_by_trend(self):
        frame, _ = three_regimes()
        profile = RegimeDetector().fit(frame).profile()

        assert (
            profile["bear"]["ret_63"]
            < profile["sideways"]["ret_63"]
            < profile["bull"]["ret_63"]
        )

    def test_the_profile_describes_each_regime(self):
        frame, _ = three_regimes()
        profile = RegimeDetector().fit(frame).profile()

        assert profile["bear"]["vol_21"] > profile["bull"]["vol_21"]
        assert sum(p["weight"] for p in profile.values()) == pytest.approx(1.0)
        assert set(profile["bull"]) == {*REGIME_FEATURES, "weight"}

    def test_probabilities_form_a_distribution(self, features):
        model = RegimeDetector().fit(features)

        proba = model.predict_proba(features)

        assert list(proba.columns) == ["bear", "sideways", "bull"]
        np.testing.assert_allclose(proba.sum(axis=1), 1.0)
        assert proba.index.equals(features.index)

    def test_predict_is_the_most_probable_regime(self, features):
        model = RegimeDetector().fit(features)

        assert (
            model.predict(features)
            == model.predict_proba(features).idxmax(axis=1).tolist()
        )

    def test_two_components_give_bear_and_bull(self, features):
        model = RegimeDetector(ModelConfig(regime_components=2)).fit(features)

        assert model.labels == ("bear", "bull")
        assert set(model.predict(features)) <= {"bear", "bull"}

    def test_is_deterministic(self, features):
        first = RegimeDetector().fit(features).predict(features)
        second = RegimeDetector().fit(features).predict(features)

        assert first == second

    def test_too_little_data(self):
        frame, _ = three_regimes(n_per=10)

        with pytest.raises(InsufficientDataError, match="at least"):
            RegimeDetector().fit(frame)

    def test_missing_columns(self, features):
        with pytest.raises(DataError, match="missing columns"):
            RegimeDetector().fit(features.drop(columns=["vol_21"]))

    def test_using_the_model_before_fitting(self, features):
        model = RegimeDetector()

        assert not model.is_fitted
        for call in (
            model.profile,
            lambda: model.predict(features),
            lambda: model.predict_proba(features),
        ):
            with pytest.raises(DataError, match="not been fitted"):
                call()


class TestAnomalyDetector:
    def test_scores_are_percentiles(self, features):
        model = AnomalyDetector().fit(features)

        scores = model.score(features)

        assert scores.min() >= 0.0
        assert scores.max() <= 1.0

    def test_an_extreme_day_scores_above_a_normal_one(self, features):
        model = AnomalyDetector().fit(features)
        normal = features.iloc[[100]].copy()
        extreme = normal.copy()
        extreme["ret_1"] = 0.25
        extreme["range_pct"] = 0.30
        extreme["gap"] = 0.15
        extreme["volume_z"] = 8.0

        assert model.score(extreme)[0] > 0.99
        assert model.score(extreme)[0] > model.score(normal)[0]
        assert model.is_anomaly(extreme)[0]

    def test_about_the_contamination_share_of_training_days_are_flagged(self, features):
        model = AnomalyDetector(ModelConfig(anomaly_contamination=0.05)).fit(features)

        flagged = model.is_anomaly(features).mean()

        assert flagged == pytest.approx(0.05, abs=0.02)

    def test_the_threshold_follows_the_contamination(self):
        assert AnomalyDetector(
            ModelConfig(anomaly_contamination=0.1)
        ).threshold == pytest.approx(0.9)

    def test_is_deterministic(self, features):
        first = AnomalyDetector().fit(features).score(features)
        second = AnomalyDetector().fit(features).score(features)

        np.testing.assert_array_equal(first, second)

    def test_scores_do_not_depend_on_other_rows_in_the_batch(self, features):
        model = AnomalyDetector().fit(features)

        alone = model.score(features.iloc[[50]])
        together = model.score(features.iloc[:100])[50]

        assert alone[0] == together

    def test_too_little_data(self, features):
        with pytest.raises(InsufficientDataError, match="at least"):
            AnomalyDetector().fit(features.iloc[:20])

    def test_missing_columns(self, features):
        with pytest.raises(DataError, match="missing columns"):
            AnomalyDetector().fit(features.drop(columns=["gap"]))

    def test_using_the_model_before_fitting(self, features):
        model = AnomalyDetector()

        assert not model.is_fitted
        with pytest.raises(DataError, match="not been fitted"):
            model.score(features)

    def test_uses_the_documented_features(self):
        assert ANOMALY_FEATURES == (
            "ret_1",
            "range_pct",
            "gap",
            "volume_z",
            "vol_ratio",
        )
