"""Training and walk-forward evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from .backtest import BacktestResult, run_backtest
from .bundle import ModelBundle
from .config import INITIAL_CAPITAL, CostModel, ModelConfig, WalkForwardConfig
from .data import fingerprint
from .evaluation import (
    classification_metrics,
    information_coefficient,
    volatility_metrics,
    walk_forward_splits,
)
from .exceptions import InsufficientDataError
from .features import WARMUP_ROWS, compute_features, make_dataset
from .models import (
    AnomalyDetector,
    DirectionModel,
    RegimeDetector,
    VolatilityForecaster,
    ewma_volatility,
    historical_volatility,
    realized_volatility,
)
from .models.volatility import log_returns
from .registry import DataInfo, ModelMetadata, ModelRegistry
from .signals import (
    DEFAULT_TARGET_VOLATILITY,
    SignalPolicy,
    probability_to_weight,
    volatility_target_weight,
)


@dataclass(frozen=True)
class EvaluationReport:
    """Out-of-sample results of a walk-forward evaluation."""

    symbol: str
    horizon: int
    folds: int
    n_oos: int
    oos_start: str
    oos_end: str
    classification: dict[str, Any]
    information_coefficient: float | None
    volatility: dict[str, dict[str, Any]]
    backtest: BacktestResult
    volatility_backtest: BacktestResult
    predictions: pd.DataFrame

    def summary(self) -> dict[str, Any]:
        """Compact JSON-safe summary (no equity curve or per-day predictions)."""
        return {
            "symbol": self.symbol,
            "horizon": self.horizon,
            "folds": self.folds,
            "n_oos": self.n_oos,
            "oos_start": self.oos_start,
            "oos_end": self.oos_end,
            "classification": self.classification,
            "information_coefficient": self.information_coefficient,
            "volatility": self.volatility,
            "strategy": self.backtest.metrics.to_dict(),
            "volatility_target": self.volatility_backtest.metrics.to_dict(),
            "benchmark": self.backtest.benchmark.to_dict(),
        }


@dataclass(frozen=True)
class TrainingResult:
    bundle: ModelBundle
    data: DataInfo
    evaluation: EvaluationReport | None


def _data_info(bars: pd.DataFrame) -> DataInfo:
    return DataInfo(
        fingerprint=fingerprint(bars),
        rows=len(bars),
        start=bars.index[0].strftime("%Y-%m-%d"),
        end=bars.index[-1].strftime("%Y-%m-%d"),
    )


def fit_bundle(
    bars: pd.DataFrame, symbol: str, model_config: ModelConfig | None = None
) -> ModelBundle:
    """Fit all four models on every row of history available."""
    config = model_config or ModelConfig()
    dataset = make_dataset(bars, config.horizon)
    direction = DirectionModel(config).fit(dataset.features, dataset.target)
    volatility = VolatilityForecaster(config).fit(bars)
    complete = compute_features(bars).dropna()
    regime = RegimeDetector(config).fit(complete)
    anomaly = AnomalyDetector(config).fit(complete)
    return ModelBundle(
        symbol=symbol,
        config=config,
        direction=direction,
        volatility=volatility,
        regime=regime,
        anomaly=anomaly,
    )


def walk_forward_evaluate(
    bars: pd.DataFrame,
    symbol: str = "",
    model_config: ModelConfig | None = None,
    walk_forward: WalkForwardConfig | None = None,
    policy: SignalPolicy | None = None,
    cost: CostModel | None = None,
    target_volatility: float = DEFAULT_TARGET_VOLATILITY,
    initial_capital: float = INITIAL_CAPITAL,
) -> EvaluationReport:
    """Evaluate the direction and volatility models strictly out of sample.

    For each fold the models are refitted on data that ends `embargo` rows before
    the test window, then predict that window. Only these out-of-sample
    predictions are scored and traded, so nothing in the report has seen its own
    outcome. Two strategies are traded: the direction model, and a position sized
    from the volatility forecast alone. Both are charged the same costs as the C++
    engine charges.
    """
    config = model_config or ModelConfig()
    wf = walk_forward or WalkForwardConfig()
    policy = policy or SignalPolicy()
    dataset = make_dataset(bars, config.horizon)
    splits = walk_forward_splits(len(dataset), wf, config.horizon)
    if not splits:
        needed = WARMUP_ROWS + wf.min_train + config.horizon + 1
        raise InsufficientDataError(
            f"{len(bars)} bars are too few for walk-forward evaluation (need about {needed})"
        )

    index = dataset.features.index
    returns = log_returns(bars)
    realised_vol = realized_volatility(returns, config.horizon)
    ewma = ewma_volatility(returns)
    historical = historical_volatility(returns)

    prob_parts: list[pd.Series] = []
    forecast_parts: list[pd.Series] = []
    for split in splits:
        train = slice(0, split.train_end)
        direction = DirectionModel(config).fit(
            dataset.features.iloc[train], dataset.target.iloc[train]
        )
        test_features = dataset.features.iloc[split.test_start : split.test_end]
        prob_parts.append(
            pd.Series(
                direction.predict_proba_up(test_features), index=test_features.index
            )
        )
        # The label of the last training row ends before the first test row, so
        # bars up to the day before the test window are safe for the HAR fit.
        history = bars.loc[: index[split.test_start - 1]]
        har = VolatilityForecaster(config).fit(history)
        forecast_parts.append(har.forecast_series(bars).loc[test_features.index])

    prob_up = pd.concat(prob_parts)
    forecast = pd.concat(forecast_parts)
    oos = pd.DataFrame(
        {
            "prob_up": prob_up,
            "forward_return": dataset.forward_return.loc[prob_up.index],
            "up": dataset.target.loc[prob_up.index],
            "forecast_vol": forecast,
            "realized_vol": realised_vol.loc[prob_up.index],
            "ewma_vol": ewma.loc[prob_up.index],
            "historical_vol": historical.loc[prob_up.index],
        }
    )
    oos["weight"] = probability_to_weight(
        oos["prob_up"].to_numpy(), policy, oos["forecast_vol"].to_numpy()
    )

    classification = classification_metrics(
        oos["up"].to_numpy(), oos["prob_up"].to_numpy()
    )
    ic = information_coefficient(
        oos["prob_up"].to_numpy(), oos["forward_return"].to_numpy()
    )
    vol = {
        "har": volatility_metrics(oos["forecast_vol"], oos["realized_vol"]),
        "ewma": volatility_metrics(oos["ewma_vol"], oos["realized_vol"]),
        "historical": volatility_metrics(oos["historical_vol"], oos["realized_vol"]),
    }

    oos["vol_target_weight"] = volatility_target_weight(
        oos["forecast_vol"].to_numpy(), target_volatility
    )
    backtest = _backtest_oos(
        bars,
        oos,
        "weight",
        symbol,
        f"ML Direction ({config.direction_model}, walk-forward)",
        cost,
        initial_capital,
    )
    volatility_backtest = _backtest_oos(
        bars,
        oos,
        "vol_target_weight",
        symbol,
        f"Volatility Target {target_volatility:.0%} (HAR, walk-forward)",
        cost,
        initial_capital,
    )
    return EvaluationReport(
        symbol=symbol,
        horizon=config.horizon,
        folds=len(splits),
        n_oos=len(oos),
        oos_start=oos.index[0].strftime("%Y-%m-%d"),
        oos_end=oos.index[-1].strftime("%Y-%m-%d"),
        classification=classification,
        information_coefficient=ic,
        volatility=vol,
        backtest=backtest,
        volatility_backtest=volatility_backtest,
        predictions=oos,
    )


def _backtest_oos(
    bars: pd.DataFrame,
    oos: pd.DataFrame,
    weight_column: str,
    symbol: str,
    name: str,
    cost: CostModel | None,
    initial_capital: float,
) -> BacktestResult:
    """Trade one column of out-of-sample weights over the span it covers.

    The window runs from the first out-of-sample day to one bar past the last, so
    the final weight earns its return. Days between folds (if any) hold no
    position.
    """
    first = bars.index.get_loc(oos.index[0])
    last = min(bars.index.get_loc(oos.index[-1]) + 1, len(bars) - 1)
    window = bars.iloc[first : last + 1]
    weights = oos[weight_column].reindex(window.index).fillna(0.0).to_numpy()
    return run_backtest(
        strategy=name,
        symbol=symbol,
        dates=[d.strftime("%Y-%m-%d") for d in window.index],
        closes=window["close"].to_numpy(),
        weights=weights,
        cost=cost,
        initial_capital=initial_capital,
    )


def train_symbol(
    bars: pd.DataFrame,
    symbol: str,
    model_config: ModelConfig | None = None,
    walk_forward: WalkForwardConfig | None = None,
    policy: SignalPolicy | None = None,
    cost: CostModel | None = None,
    evaluate: bool = True,
    target_volatility: float = DEFAULT_TARGET_VOLATILITY,
) -> TrainingResult:
    """Fit the final bundle and, optionally, evaluate the recipe out of sample."""
    config = model_config or ModelConfig()
    evaluation = (
        walk_forward_evaluate(
            bars, symbol, config, walk_forward, policy, cost, target_volatility
        )
        if evaluate
        else None
    )
    bundle = fit_bundle(bars, symbol, config)
    return TrainingResult(bundle=bundle, data=_data_info(bars), evaluation=evaluation)


def train_and_save(
    registry: ModelRegistry,
    bars: pd.DataFrame,
    symbol: str,
    model_config: ModelConfig | None = None,
    walk_forward: WalkForwardConfig | None = None,
    policy: SignalPolicy | None = None,
    cost: CostModel | None = None,
    evaluate: bool = True,
    target_volatility: float = DEFAULT_TARGET_VOLATILITY,
) -> tuple[ModelMetadata, EvaluationReport | None]:
    """Train a symbol and store the bundle with its evaluation summary."""
    result = train_symbol(
        bars,
        symbol,
        model_config,
        walk_forward,
        policy,
        cost,
        evaluate,
        target_volatility,
    )
    metrics = result.evaluation.summary() if result.evaluation is not None else {}
    metadata = registry.save(result.bundle, result.data, metrics)
    return metadata, result.evaluation


__all__ = [
    "EvaluationReport",
    "TrainingResult",
    "fit_bundle",
    "train_and_save",
    "train_symbol",
    "walk_forward_evaluate",
]
