import numpy as np
import pandas as pd
import pytest
from ai_models.config import ModelConfig
from ai_models.exceptions import DataError, InsufficientDataError
from ai_models.features import FEATURE_COLUMNS, make_dataset
from ai_models.models import DirectionModel
from ai_models.models.direction import MIN_TRAIN_ROWS, WINSOR_LIMIT
from sklearn.metrics import roc_auc_score


@pytest.fixture(scope="module")
def dataset(bars):
    return make_dataset(bars, 1)


def planted(n=600, seed=0):
    """Features where the label is mostly determined by two of them."""
    rng = np.random.default_rng(seed)
    x = pd.DataFrame(rng.standard_normal((n, 4)), columns=["a", "b", "c", "d"])
    logit = 2.5 * x["a"] - 1.5 * x["b"]
    y = pd.Series((rng.random(n) < 1 / (1 + np.exp(-logit))).astype(int), name="up")
    return x, y


class TestFitAndPredict:
    def test_probabilities_are_proper(self, dataset):
        model = DirectionModel().fit(dataset.features, dataset.target)

        proba = model.predict_proba_up(dataset.features)

        assert proba.shape == (len(dataset),)
        assert ((proba > 0) & (proba < 1)).all()

    def test_is_fitted_only_after_fit(self, dataset):
        model = DirectionModel()

        assert not model.is_fitted
        assert model.fit(dataset.features, dataset.target).is_fitted

    def test_fit_returns_the_model_for_chaining(self, dataset):
        model = DirectionModel()

        assert model.fit(dataset.features, dataset.target) is model

    def test_remembers_the_feature_names(self, dataset):
        model = DirectionModel().fit(dataset.features, dataset.target)

        assert model.feature_names == FEATURE_COLUMNS

    def test_is_deterministic(self, dataset):
        first = (
            DirectionModel()
            .fit(dataset.features, dataset.target)
            .predict_proba_up(dataset.features)
        )
        second = (
            DirectionModel()
            .fit(dataset.features, dataset.target)
            .predict_proba_up(dataset.features)
        )

        np.testing.assert_array_equal(first, second)

    def test_column_order_does_not_matter(self, dataset):
        model = DirectionModel().fit(dataset.features, dataset.target)
        shuffled = dataset.features[list(reversed(dataset.features.columns))]

        np.testing.assert_allclose(
            model.predict_proba_up(shuffled), model.predict_proba_up(dataset.features)
        )

    def test_recovers_a_planted_signal(self):
        x, y = planted()
        model = DirectionModel().fit(x.iloc[:400], y.iloc[:400])

        auc = roc_auc_score(y.iloc[400:], model.predict_proba_up(x.iloc[400:]))

        assert auc > 0.80

    def test_gradient_boosting_also_learns_a_planted_signal(self):
        x, y = planted()
        model = DirectionModel(ModelConfig(direction_model="gradient_boosting"))
        model.fit(x.iloc[:400], y.iloc[:400])

        assert roc_auc_score(y.iloc[400:], model.predict_proba_up(x.iloc[400:])) > 0.75

    def test_an_extreme_outlier_cannot_dominate_the_prediction(self):
        x, y = planted()
        model = DirectionModel().fit(x, y)
        row = x.iloc[[0]].copy()
        big, bigger = row.copy(), row.copy()
        big["a"], bigger["a"] = 50.0, 5000.0

        np.testing.assert_allclose(
            model.predict_proba_up(big), model.predict_proba_up(bigger)
        )
        assert WINSOR_LIMIT == 5.0


class TestValidation:
    def test_too_few_rows(self):
        x, y = planted(n=MIN_TRAIN_ROWS - 1)

        with pytest.raises(InsufficientDataError, match="at least"):
            DirectionModel().fit(x, y)

    def test_a_single_class(self):
        x, _ = planted()

        with pytest.raises(InsufficientDataError, match="single class"):
            DirectionModel().fit(x, pd.Series(np.ones(len(x), dtype=int)))

    def test_mismatched_lengths(self):
        x, y = planted()

        with pytest.raises(DataError, match="same length"):
            DirectionModel().fit(x, y.iloc[:-1])

    def test_predicting_before_fitting(self):
        with pytest.raises(DataError, match="not been fitted"):
            DirectionModel().predict_proba_up(planted()[0])

    def test_missing_columns_at_prediction_time(self, dataset):
        model = DirectionModel().fit(dataset.features, dataset.target)

        with pytest.raises(DataError, match="missing columns"):
            model.predict_proba_up(dataset.features.drop(columns=["rsi_14"]))


class TestExplanations:
    def test_contributions_reconstruct_the_prediction_exactly(self, dataset):
        model = DirectionModel().fit(dataset.features, dataset.target)
        row = dataset.features.iloc[[100]]

        contributions = model.contributions(row)
        logit = model.intercept() + sum(contributions.values())

        probability = model.predict_proba_up(row)[0]
        assert 1.0 / (1.0 + np.exp(-logit)) == pytest.approx(probability)

    def test_contributions_cover_every_feature(self, dataset):
        model = DirectionModel().fit(dataset.features, dataset.target)

        assert set(model.contributions(dataset.features.iloc[[0]])) == set(
            FEATURE_COLUMNS
        )

    def test_the_planted_features_dominate_the_importance(self):
        x, y = planted()
        model = DirectionModel().fit(x, y)

        importance = model.feature_importance()

        assert sum(importance.values()) == pytest.approx(1.0)
        assert importance["a"] > importance["c"]
        assert importance["a"] > importance["d"]
        assert importance["b"] > importance["d"]

    def test_gradient_boosting_has_importances_but_no_contributions(self):
        x, y = planted()
        model = DirectionModel(ModelConfig(direction_model="gradient_boosting")).fit(
            x, y
        )

        assert not model.supports_contributions
        assert model.contributions(x.iloc[[0]]) == {}
        assert model.intercept() == 0.0
        assert sum(model.feature_importance().values()) == pytest.approx(1.0)

    def test_contributions_explain_exactly_one_row(self, dataset):
        model = DirectionModel().fit(dataset.features, dataset.target)

        with pytest.raises(DataError, match="exactly one row"):
            model.contributions(dataset.features.iloc[:2])

    def test_importance_before_fitting(self):
        with pytest.raises(DataError, match="not been fitted"):
            DirectionModel().feature_importance()
