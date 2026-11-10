"""AlphaForge AI models.

Causal features, four forecasting models (direction, volatility, regime and
anomaly), walk-forward evaluation, and a backtester that charges the same costs
as the C++ engine.
"""

__version__ = "1.0.0"

from .backtest import BacktestResult, run_backtest, simulate
from .bundle import ModelBundle
from .config import CostModel, ModelConfig, WalkForwardConfig
from .data import list_symbols, load_symbol, read_bars, validate_bars
from .exceptions import (
    AiModelsError,
    ArtifactError,
    DataError,
    DataNotFoundError,
    InsufficientDataError,
    ModelNotFoundError,
)
from .features import FEATURE_COLUMNS, compute_features, make_dataset
from .inference import Prediction, Predictor
from .metrics import PerformanceMetrics, summarize
from .pipeline import (
    EvaluationReport,
    fit_bundle,
    train_and_save,
    train_symbol,
    walk_forward_evaluate,
)
from .registry import DataInfo, ModelMetadata, ModelRegistry
from .signals import SignalPolicy, probability_to_weight, volatility_target_weight

__all__ = [
    "FEATURE_COLUMNS",
    "AiModelsError",
    "ArtifactError",
    "BacktestResult",
    "CostModel",
    "DataError",
    "DataInfo",
    "DataNotFoundError",
    "EvaluationReport",
    "InsufficientDataError",
    "ModelBundle",
    "ModelConfig",
    "ModelMetadata",
    "ModelNotFoundError",
    "ModelRegistry",
    "PerformanceMetrics",
    "Prediction",
    "Predictor",
    "SignalPolicy",
    "WalkForwardConfig",
    "__version__",
    "compute_features",
    "fit_bundle",
    "list_symbols",
    "load_symbol",
    "make_dataset",
    "probability_to_weight",
    "read_bars",
    "run_backtest",
    "simulate",
    "summarize",
    "train_and_save",
    "train_symbol",
    "validate_bars",
    "volatility_target_weight",
    "walk_forward_evaluate",
]
