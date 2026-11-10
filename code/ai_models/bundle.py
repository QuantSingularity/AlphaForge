"""The set of fitted models for one symbol."""

from __future__ import annotations

from dataclasses import dataclass

from .config import ModelConfig
from .models import (
    AnomalyDetector,
    DirectionModel,
    RegimeDetector,
    VolatilityForecaster,
)


@dataclass
class ModelBundle:
    """Everything needed to produce a prediction for one symbol."""

    symbol: str
    config: ModelConfig
    direction: DirectionModel
    volatility: VolatilityForecaster
    regime: RegimeDetector
    anomaly: AnomalyDetector
