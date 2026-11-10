"""Unsupervised market regime detection.

A Gaussian mixture is fitted to three slow-moving state variables: the 63-day
trend, the 21-day volatility and the drawdown from the 63-day high. Components
are then named by their average trend, so the labels have a stable meaning:
the lowest-trend component is "bear" and the highest is "bull".
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.mixture import GaussianMixture
from sklearn.preprocessing import StandardScaler

from ..config import ModelConfig
from ..exceptions import DataError, InsufficientDataError

REGIME_FEATURES = ("ret_63", "vol_21", "drawdown_63")
MIN_TRAIN_ROWS = 60
_LABELS = {2: ("bear", "bull"), 3: ("bear", "sideways", "bull")}


class RegimeDetector:
    def __init__(self, config: ModelConfig | None = None) -> None:
        self.config = config or ModelConfig()
        self.labels: tuple[str, ...] = ()
        self._scaler: StandardScaler | None = None
        self._mixture: GaussianMixture | None = None
        self._component_to_label: dict[int, str] = {}
        self._profile: dict[str, dict[str, float]] = {}

    def _matrix(self, features: pd.DataFrame) -> np.ndarray:
        missing = [c for c in REGIME_FEATURES if c not in features.columns]
        if missing:
            raise DataError(f"features are missing columns: {', '.join(missing)}")
        return features[list(REGIME_FEATURES)].to_numpy(dtype=float)

    def fit(self, features: pd.DataFrame) -> RegimeDetector:
        matrix = self._matrix(features)
        if len(matrix) < MIN_TRAIN_ROWS:
            raise InsufficientDataError(
                f"need at least {MIN_TRAIN_ROWS} rows to fit the regime model, got {len(matrix)}"
            )
        scaler = StandardScaler().fit(matrix)
        mixture = GaussianMixture(
            n_components=self.config.regime_components,
            covariance_type="full",
            n_init=5,
            reg_covar=1e-4,
            random_state=self.config.random_state,
        ).fit(scaler.transform(matrix))

        centres = scaler.inverse_transform(mixture.means_)
        trend_column = REGIME_FEATURES.index("ret_63")
        by_trend = np.argsort(centres[:, trend_column])
        names = _LABELS[self.config.regime_components]
        self._component_to_label = {
            int(c): names[rank] for rank, c in enumerate(by_trend)
        }
        self.labels = names
        self._profile = {
            self._component_to_label[c]: {
                name: float(centres[c, i]) for i, name in enumerate(REGIME_FEATURES)
            }
            | {"weight": float(mixture.weights_[c])}
            for c in range(len(centres))
        }
        self._scaler, self._mixture = scaler, mixture
        return self

    @property
    def is_fitted(self) -> bool:
        return self._mixture is not None

    def profile(self) -> dict[str, dict[str, float]]:
        """Average state variables and share of history for each regime."""
        if not self.is_fitted:
            raise DataError("the regime model has not been fitted")
        return {label: dict(values) for label, values in self._profile.items()}

    def predict_proba(self, features: pd.DataFrame) -> pd.DataFrame:
        """Posterior probability of each regime, one row per input row."""
        if self._scaler is None or self._mixture is None:
            raise DataError("the regime model has not been fitted")
        posterior = self._mixture.predict_proba(
            self._scaler.transform(self._matrix(features))
        )
        frame = pd.DataFrame(
            index=features.index, columns=list(self.labels), dtype=float
        )
        for component, label in self._component_to_label.items():
            frame[label] = posterior[:, component]
        return frame[list(self.labels)]

    def predict(self, features: pd.DataFrame) -> list[str]:
        """Most probable regime for each row."""
        probabilities = self.predict_proba(features)
        return [str(label) for label in probabilities.idxmax(axis=1)]
