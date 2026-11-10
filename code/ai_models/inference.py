"""Turning a fitted bundle and recent bars into predictions."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from .bundle import ModelBundle
from .features import latest_features
from .signals import SignalPolicy, probability_to_weight


@dataclass(frozen=True)
class Prediction:
    """What the models say about one trading day, using data up to that day."""

    date: str
    prob_up: float
    signal_weight: float
    forecast_vol: float | None
    regime: str
    regime_probabilities: dict[str, float]
    anomaly_score: float
    is_anomaly: bool
    contributions: dict[str, float] | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class Predictor:
    """Produces predictions for the most recent bars of a symbol."""

    def __init__(self, bundle: ModelBundle, policy: SignalPolicy | None = None) -> None:
        self.bundle = bundle
        self.policy = policy or SignalPolicy()

    def predict(
        self, bars: pd.DataFrame, rows: int = 1, explain: bool = False
    ) -> list[Prediction]:
        """Predictions for the last `rows` days that have a full feature set.

        With explain=True the final prediction also carries each feature's
        contribution to the up-move log-odds (logistic model only).
        """
        features = latest_features(bars, rows)
        bundle = self.bundle
        prob_up = bundle.direction.predict_proba_up(features)
        forecast = bundle.volatility.forecast_series(bars).reindex(features.index)
        regime_probabilities = bundle.regime.predict_proba(features)
        regimes = regime_probabilities.idxmax(axis=1)
        anomaly_score = bundle.anomaly.score(features)
        flagged = bundle.anomaly.is_anomaly(features)
        weights = probability_to_weight(prob_up, self.policy, forecast.to_numpy())

        predictions: list[Prediction] = []
        for i, (date, vol) in enumerate(forecast.items()):
            contributions = None
            if explain and i == len(features) - 1:
                contributions = (
                    bundle.direction.contributions(features.iloc[[i]]) or None
                )
            predictions.append(
                Prediction(
                    date=pd.Timestamp(date).strftime("%Y-%m-%d"),
                    prob_up=float(prob_up[i]),
                    signal_weight=float(weights[i]),
                    forecast_vol=None if not np.isfinite(vol) else float(vol),
                    regime=str(regimes.iloc[i]),
                    regime_probabilities={
                        label: float(p)
                        for label, p in regime_probabilities.iloc[i].items()
                    },
                    anomaly_score=float(anomaly_score[i]),
                    is_anomaly=bool(flagged[i]),
                    contributions=contributions,
                )
            )
        return predictions

    def latest(self, bars: pd.DataFrame, explain: bool = True) -> Prediction:
        return self.predict(bars, rows=1, explain=explain)[-1]
