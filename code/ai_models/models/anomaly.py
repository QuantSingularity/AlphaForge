"""Anomaly scoring for unusual trading days."""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from ..config import ModelConfig
from ..exceptions import DataError, InsufficientDataError

ANOMALY_FEATURES = ("ret_1", "range_pct", "gap", "volume_z", "vol_ratio")
MIN_TRAIN_ROWS = 60


class AnomalyDetector:
    """Scores each day by how unusual it is compared with the training history.

    The raw isolation forest score is converted to a percentile of the training
    scores, so 0.97 means "more unusual than 97% of the days the model saw". A
    day is flagged when its score falls in the top `contamination` share.
    """

    def __init__(self, config: ModelConfig | None = None) -> None:
        self.config = config or ModelConfig()
        self._forest: IsolationForest | None = None
        self._training_scores: np.ndarray = np.empty(0)

    def _matrix(self, features: pd.DataFrame) -> np.ndarray:
        missing = [c for c in ANOMALY_FEATURES if c not in features.columns]
        if missing:
            raise DataError(f"features are missing columns: {', '.join(missing)}")
        return features[list(ANOMALY_FEATURES)].to_numpy(dtype=float)

    def fit(self, features: pd.DataFrame) -> AnomalyDetector:
        matrix = self._matrix(features)
        if len(matrix) < MIN_TRAIN_ROWS:
            raise InsufficientDataError(
                f"need at least {MIN_TRAIN_ROWS} rows to fit the anomaly model, got {len(matrix)}"
            )
        forest = IsolationForest(
            n_estimators=200,
            contamination="auto",
            random_state=self.config.random_state,
        ).fit(matrix)
        self._forest = forest
        self._training_scores = np.sort(-forest.score_samples(matrix))
        return self

    @property
    def is_fitted(self) -> bool:
        return self._forest is not None

    @property
    def threshold(self) -> float:
        """Percentile score above which a day is flagged."""
        return 1.0 - self.config.anomaly_contamination

    def score(self, features: pd.DataFrame) -> np.ndarray:
        """Anomaly percentile in [0, 1] for each row; higher is more unusual."""
        if self._forest is None:
            raise DataError("the anomaly model has not been fitted")
        raw = -self._forest.score_samples(self._matrix(features))
        ranks = np.searchsorted(self._training_scores, raw, side="right")
        return ranks / self._training_scores.size

    def is_anomaly(self, features: pd.DataFrame) -> np.ndarray:
        return self.score(features) >= self.threshold
