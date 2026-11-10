"""Configuration and defaults.

The cost model and the annualisation constants deliberately mirror the C++
engine (code/backend/include/core/Config.hpp) so that a strategy backtested here
is charged exactly like one backtested by the engine.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

TRADING_DAYS_PER_YEAR = 252
RISK_FREE_RATE = 0.02
INITIAL_CAPITAL = 1_000_000.0

DATA_DIR_ENV = "ALPHAFORGE_DATA_DIR"
ARTIFACTS_DIR_ENV = "ALPHAFORGE_MODEL_DIR"

DIRECTION_MODELS = ("logistic", "gradient_boosting")


def repo_root() -> Path:
    """Repository root, derived from this file's location (code/ai_models/)."""
    return Path(__file__).resolve().parents[2]


def default_data_dir() -> Path:
    """Directory holding the daily OHLCV CSV files (one per symbol)."""
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return Path(override)
    return repo_root() / "code" / "backend" / "data"


def default_artifacts_dir() -> Path:
    """Directory where trained model bundles are stored."""
    override = os.environ.get(ARTIFACTS_DIR_ENV)
    if override:
        return Path(override)
    return repo_root() / "code" / "ai_models" / "artifacts"


@dataclass(frozen=True)
class CostModel:
    """Per-trade costs, expressed in basis points of traded notional."""

    transaction_cost_bps: float = 5.0
    slippage_bps: float = 2.0

    def __post_init__(self) -> None:
        if self.transaction_cost_bps < 0 or self.slippage_bps < 0:
            raise ValueError("costs must be non-negative")

    @property
    def rate(self) -> float:
        """Total cost per unit of turnover, as a fraction."""
        return (self.transaction_cost_bps + self.slippage_bps) / 10_000.0


@dataclass(frozen=True)
class ModelConfig:
    """Hyper-parameters shared by training and evaluation."""

    horizon: int = 1
    direction_model: str = "logistic"
    logistic_c: float = 0.05
    regime_components: int = 3
    anomaly_contamination: float = 0.025
    volatility_alpha: float = 1.0
    random_state: int = 7

    def __post_init__(self) -> None:
        if self.horizon < 1:
            raise ValueError("horizon must be at least 1 day")
        if self.direction_model not in DIRECTION_MODELS:
            raise ValueError(f"direction_model must be one of {DIRECTION_MODELS}")
        if self.logistic_c <= 0:
            raise ValueError("logistic_c must be positive")
        if self.regime_components not in (2, 3):
            raise ValueError("regime_components must be 2 or 3")
        if not 0.0 < self.anomaly_contamination < 0.5:
            raise ValueError("anomaly_contamination must be in (0, 0.5)")
        if self.volatility_alpha < 0:
            raise ValueError("volatility_alpha must be non-negative")


@dataclass(frozen=True)
class WalkForwardConfig:
    """Expanding-window walk-forward evaluation settings.

    embargo is the number of rows removed between the end of the training window
    and the start of a test window. It must be at least the label horizon, so no
    training label looks into the test period. None means "use the horizon".
    """

    min_train: int = 252
    test_size: int = 21
    step: int = 21
    embargo: int | None = None

    def __post_init__(self) -> None:
        if self.min_train < 30:
            raise ValueError("min_train must be at least 30 rows")
        if self.test_size < 1 or self.step < 1:
            raise ValueError("test_size and step must be positive")
        if self.step < self.test_size:
            raise ValueError(
                "step must be at least test_size so test windows do not overlap"
            )
        if self.embargo is not None and self.embargo < 0:
            raise ValueError("embargo must be non-negative")
