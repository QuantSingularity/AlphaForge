"""Walk-forward splitting and out-of-sample metrics."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    brier_score_loss,
    log_loss,
    roc_auc_score,
)

from .config import WalkForwardConfig


@dataclass(frozen=True)
class Split:
    """Row ranges of one fold: train on [0, train_end), test on [test_start, test_end)."""

    train_end: int
    test_start: int
    test_end: int


def walk_forward_splits(
    n_rows: int, config: WalkForwardConfig | None = None, horizon: int = 1
) -> list[Split]:
    """Expanding-window folds with an embargo between training and test data.

    A label at row i is the return over the next `horizon` rows, so the last
    `horizon` training labels would otherwise reach into the test window. The
    embargo removes those rows from training, which keeps every fold free of
    that overlap.
    """
    config = config or WalkForwardConfig()
    if horizon < 1:
        raise ValueError("horizon must be at least 1")
    embargo = horizon if config.embargo is None else config.embargo
    if embargo < horizon:
        raise ValueError("embargo must be at least the label horizon")

    splits: list[Split] = []
    test_start = config.min_train + embargo
    while test_start < n_rows:
        splits.append(
            Split(
                train_end=test_start - embargo,
                test_start=test_start,
                test_end=min(test_start + config.test_size, n_rows),
            )
        )
        test_start += config.step
    return splits


def _number(value: float | np.floating | None) -> float | None:
    """A finite float, or None, so reports are always valid JSON."""
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def classification_metrics(
    y_true: np.ndarray, prob_up: np.ndarray
) -> dict[str, float | int | None]:
    """Quality of predicted up-probabilities against realised outcomes."""
    y = np.asarray(y_true, dtype=int)
    p = np.clip(np.asarray(prob_up, dtype=float), 1e-6, 1.0 - 1e-6)
    if y.size == 0:
        raise ValueError("no observations to score")
    predicted = (p >= 0.5).astype(int)
    base_rate = float(y.mean())
    accuracy = float(accuracy_score(y, predicted))
    single_class = np.unique(y).size < 2
    return {
        "n": int(y.size),
        "accuracy": accuracy,
        "balanced_accuracy": (
            None if single_class else _number(balanced_accuracy_score(y, predicted))
        ),
        "auc": None if single_class else _number(roc_auc_score(y, p)),
        "brier": _number(brier_score_loss(y, p)),
        "log_loss": _number(log_loss(y, p, labels=[0, 1])),
        "base_rate": base_rate,
        # Accuracy above what always predicting the majority class would score.
        "edge_vs_majority": accuracy - max(base_rate, 1.0 - base_rate),
    }


def information_coefficient(score: np.ndarray, realised: np.ndarray) -> float | None:
    """Spearman rank correlation between a signal and the return that followed."""
    frame = pd.DataFrame({"score": score, "realised": realised}).dropna()
    if (
        len(frame) < 3
        or frame["score"].nunique() < 2
        or frame["realised"].nunique() < 2
    ):
        return None
    return _number(frame["score"].corr(frame["realised"], method="spearman"))


def volatility_metrics(
    forecast: np.ndarray, realised: np.ndarray
) -> dict[str, float | int | None]:
    """Error of annualised volatility forecasts against realised volatility.

    QLIKE is the standard loss for variance forecasts (0 is perfect); it penalises
    under-prediction of risk more than over-prediction.
    """
    frame = pd.DataFrame({"f": forecast, "r": realised}).dropna()
    frame = frame[(frame["f"] > 0.0) & (frame["r"] > 0.0)]
    if frame.empty:
        return {"n": 0, "rmse": None, "mae": None, "qlike": None}
    error = frame["f"] - frame["r"]
    ratio = (frame["r"] / frame["f"]) ** 2
    return {
        "n": int(len(frame)),
        "rmse": _number(np.sqrt(np.mean(error**2))),
        "mae": _number(np.mean(np.abs(error))),
        "qlike": _number(np.mean(ratio - np.log(ratio) - 1.0)),
    }
