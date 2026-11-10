"""Request and response models of the HTTP API."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

# Same alphabet the data layer accepts, enforced at the edge so a bad symbol is
# rejected before it gets anywhere near a file path.
SYMBOL_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,15}$"
SymbolStr = Annotated[str, Field(pattern=SYMBOL_PATTERN)]


class Health(BaseModel):
    status: str
    version: str
    models: int
    data_symbols: int
    training_enabled: bool
    auto_train: bool


class DataSpan(BaseModel):
    rows: int
    start: str
    end: str


class ModelInfo(BaseModel):
    symbol: str
    version: str
    trained_at: str
    data: DataSpan
    stale: bool
    evaluation: dict[str, Any]


class ModelsResponse(BaseModel):
    models: list[ModelInfo]


class Contribution(BaseModel):
    feature: str
    contribution: float


class PredictionOut(BaseModel):
    symbol: str
    model_version: str
    date: str
    prob_up: float
    signal_weight: float
    forecast_vol: float | None
    regime: str
    regime_probabilities: dict[str, float]
    anomaly_score: float
    is_anomaly: bool
    intercept: float | None = None
    explanation: list[Contribution] = []


class Signal(BaseModel):
    symbol: str
    date: str
    prob_up: float
    signal_weight: float
    forecast_vol: float | None
    regime: str
    anomaly_score: float
    is_anomaly: bool


class SignalError(BaseModel):
    symbol: str
    error: str


class SignalsResponse(BaseModel):
    signals: list[Signal]
    errors: list[SignalError]


class HistoryPoint(BaseModel):
    date: str
    close: float
    prob_up: float
    signal_weight: float
    forecast_vol: float | None
    regime: str
    anomaly_score: float
    is_anomaly: bool


class HistoryResponse(BaseModel):
    symbol: str
    model_version: str
    points: list[HistoryPoint]


class BacktestRequest(BaseModel):
    """Same cost and capital fields as the C++ engine's /api/backtest."""

    model_config = ConfigDict(extra="forbid")

    symbol: SymbolStr
    transaction_cost_bps: float = Field(5.0, ge=0.0, le=1000.0)
    slippage_bps: float = Field(2.0, ge=0.0, le=1000.0)
    initial_capital: float = Field(1_000_000.0, gt=0.0, le=1e12)
    target_volatility: float = Field(0.12, gt=0.0, le=2.0)
    long_threshold: float = Field(0.55, gt=0.0, lt=1.0)


class BacktestResponse(BaseModel):
    symbol: str
    model_version: str | None
    evaluation: dict[str, Any]
    direction: dict[str, Any]
    volatility_target: dict[str, Any]


class TrainRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbols: list[SymbolStr] | None = Field(None, max_length=50)


class TrainedModel(BaseModel):
    symbol: str
    version: str
    rows: int
    evaluation: dict[str, Any]


class TrainResponse(BaseModel):
    trained: list[TrainedModel]
