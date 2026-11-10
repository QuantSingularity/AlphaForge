"""Classifier for the sign of the next return."""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, StandardScaler

from ..config import ModelConfig
from ..exceptions import DataError, InsufficientDataError

MIN_TRAIN_ROWS = 60
# Standardised features are clipped to +/- this many standard deviations so a
# single extreme day cannot dominate the fit.
WINSOR_LIMIT = 5.0


def _winsorize(values: np.ndarray) -> np.ndarray:
    return np.clip(values, -WINSOR_LIMIT, WINSOR_LIMIT)


class DirectionModel:
    """Estimates the probability that the next return is positive.

    The default is a strongly regularised logistic regression: with only a few
    hundred daily rows, a small, interpretable model is the honest choice. A
    gradient boosting variant is available for comparison.
    """

    def __init__(self, config: ModelConfig | None = None) -> None:
        self.config = config or ModelConfig()
        self.feature_names: tuple[str, ...] = ()
        self._pipeline: Pipeline | None = None

    # ------------------------------------------------------------------ fit
    def _build(self) -> Pipeline:
        cfg = self.config
        if cfg.direction_model == "logistic":
            estimator = LogisticRegression(
                C=cfg.logistic_c, max_iter=1000, random_state=cfg.random_state
            )
        else:
            estimator = GradientBoostingClassifier(
                n_estimators=100,
                max_depth=2,
                learning_rate=0.05,
                subsample=0.8,
                random_state=cfg.random_state,
            )
        return Pipeline(
            [
                ("scale", StandardScaler()),
                ("winsorize", FunctionTransformer(_winsorize)),
                ("estimator", estimator),
            ]
        )

    def fit(self, features: pd.DataFrame, target: pd.Series) -> DirectionModel:
        if len(features) != len(target):
            raise DataError("features and target must have the same length")
        if len(features) < MIN_TRAIN_ROWS:
            raise InsufficientDataError(
                f"need at least {MIN_TRAIN_ROWS} training rows, got {len(features)}"
            )
        if target.nunique() < 2:
            raise InsufficientDataError("training labels contain a single class")
        self.feature_names = tuple(features.columns)
        pipeline = self._build()
        pipeline.fit(features.to_numpy(dtype=float), target.to_numpy(dtype=int))
        self._pipeline = pipeline
        return self

    # ------------------------------------------------------------- inference
    @property
    def is_fitted(self) -> bool:
        return self._pipeline is not None

    def _matrix(self, features: pd.DataFrame) -> np.ndarray:
        if self._pipeline is None:
            raise DataError("the direction model has not been fitted")
        missing = [name for name in self.feature_names if name not in features.columns]
        if missing:
            raise DataError(f"features are missing columns: {', '.join(missing)}")
        return features[list(self.feature_names)].to_numpy(dtype=float)

    def predict_proba_up(self, features: pd.DataFrame) -> np.ndarray:
        """Probability of a positive next return for each row."""
        matrix = self._matrix(features)
        assert self._pipeline is not None
        return self._pipeline.predict_proba(matrix)[:, 1]

    @property
    def supports_contributions(self) -> bool:
        return self.config.direction_model == "logistic"

    def contributions(self, row: pd.DataFrame) -> dict[str, float]:
        """Each feature's contribution to the log-odds of an up move for one row.

        Only defined for the logistic model, where the prediction is exactly the
        intercept plus the sum of these terms. Other models return an empty dict.
        """
        if not self.supports_contributions:
            return {}
        matrix = self._matrix(row)
        if matrix.shape[0] != 1:
            raise DataError("contributions() explains exactly one row")
        assert self._pipeline is not None
        scaled = self._pipeline[:-1].transform(matrix)[0]
        coefficients = self._pipeline.named_steps["estimator"].coef_[0]
        return {
            name: float(coef * value)
            for name, coef, value in zip(self.feature_names, coefficients, scaled)
        }

    def intercept(self) -> float:
        if not self.supports_contributions or self._pipeline is None:
            return 0.0
        return float(self._pipeline.named_steps["estimator"].intercept_[0])

    def feature_importance(self) -> dict[str, float]:
        """Relative importance of each feature, summing to 1."""
        if self._pipeline is None:
            raise DataError("the direction model has not been fitted")
        estimator = self._pipeline.named_steps["estimator"]
        if self.supports_contributions:
            raw = np.abs(estimator.coef_[0])
        else:
            raw = np.asarray(estimator.feature_importances_, dtype=float)
        total = float(raw.sum())
        weights = raw / total if total > 0 else np.full(raw.shape, 1.0 / raw.size)
        return {name: float(w) for name, w in zip(self.feature_names, weights)}
