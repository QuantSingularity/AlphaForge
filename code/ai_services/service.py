"""The service layer: data and model caching, training and predictions.

AiService holds no HTTP concepts. It returns plain dictionaries, which the app
layer validates against the response models, and raises ai_models exceptions,
which the app layer maps to status codes.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from typing import Any

import pandas as pd
from ai_models import __version__ as models_version
from ai_models.bundle import ModelBundle
from ai_models.config import CostModel, ModelConfig
from ai_models.data import (
    fingerprint,
    list_symbols,
    normalise_symbol,
    read_bars,
    symbol_path,
)
from ai_models.exceptions import AiModelsError, DataNotFoundError
from ai_models.features import compute_features
from ai_models.inference import Predictor
from ai_models.pipeline import train_and_save, walk_forward_evaluate
from ai_models.registry import ModelMetadata, ModelRegistry
from ai_models.signals import SignalPolicy

from . import __version__
from .settings import Settings

log = logging.getLogger("ai_services")

MAX_HISTORY_ROWS = 500
_BACKTEST_CACHE_SIZE = 32
_EXPLANATION_ITEMS = 8


class TrainingBusyError(AiModelsError):
    """Another training run is already in progress."""


class TrainingDisabledError(AiModelsError):
    """Training through the API is switched off (AI_ALLOW_TRAINING=0)."""


class AiService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.registry = ModelRegistry(settings.artifacts_dir)
        self._bars: dict[str, tuple[tuple[int, int], pd.DataFrame]] = {}
        self._bundles: dict[str, tuple[ModelMetadata, ModelBundle]] = {}
        self._backtests: OrderedDict[tuple[Any, ...], dict[str, Any]] = OrderedDict()
        self._cache_lock = threading.Lock()
        self._train_lock = threading.Lock()

    # ------------------------------------------------------------------ data
    def data_symbols(self) -> list[str]:
        return list_symbols(self.settings.data_dir)

    def bars(self, symbol: str) -> pd.DataFrame:
        """Bars for a symbol, re-read only when the file changes."""
        canonical = normalise_symbol(symbol)
        path = symbol_path(canonical, self.settings.data_dir)
        stat = path.stat()
        key = (stat.st_mtime_ns, stat.st_size)
        with self._cache_lock:
            cached = self._bars.get(canonical)
            if cached is not None and cached[0] == key:
                return cached[1]
        frame = read_bars(path)
        with self._cache_lock:
            self._bars[canonical] = (key, frame)
        return frame

    # ---------------------------------------------------------------- models
    def trained_symbols(self) -> list[str]:
        return self.registry.list_symbols()

    def _loaded(self, symbol: str) -> tuple[ModelMetadata, ModelBundle]:
        """The latest bundle for a symbol, loaded once per version."""
        canonical = normalise_symbol(symbol)
        version = self.registry.latest_version(canonical)
        with self._cache_lock:
            cached = self._bundles.get(canonical)
            if cached is not None and cached[0].version == version:
                return cached
        entry = (
            self.registry.metadata(canonical, version),
            self.registry.load(canonical, version),
        )
        with self._cache_lock:
            self._bundles[canonical] = entry
        return entry

    def is_stale(self, symbol: str, metadata: ModelMetadata | None = None) -> bool:
        """True when the data on disk differs from the data the model saw."""
        metadata = metadata or self.registry.metadata(symbol)
        try:
            return fingerprint(self.bars(symbol)) != metadata.data.fingerprint
        except DataNotFoundError:
            return False

    # ------------------------------------------------------------ operations
    def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "version": f"{__version__} (ai_models {models_version})",
            "models": len(self.trained_symbols()),
            "data_symbols": len(self.data_symbols()),
            "training_enabled": self.settings.allow_training,
            "auto_train": self.settings.auto_train,
        }

    def models(self) -> list[dict[str, Any]]:
        out = []
        for symbol in self.trained_symbols():
            meta = self.registry.metadata(symbol)
            out.append(
                {
                    "symbol": symbol,
                    "version": meta.version,
                    "trained_at": meta.trained_at,
                    "data": {
                        "rows": meta.data.rows,
                        "start": meta.data.start,
                        "end": meta.data.end,
                    },
                    "stale": self.is_stale(symbol, meta),
                    "evaluation": meta.metrics,
                }
            )
        return out

    def predict(self, symbol: str) -> dict[str, Any]:
        canonical = normalise_symbol(symbol)
        metadata, bundle = self._loaded(canonical)
        prediction = Predictor(bundle).latest(self.bars(canonical), explain=True)
        payload = prediction.to_dict()
        contributions = payload.pop("contributions") or {}
        ranked = sorted(contributions.items(), key=lambda kv: abs(kv[1]), reverse=True)
        return {
            "symbol": canonical,
            "model_version": metadata.version,
            **payload,
            "intercept": bundle.direction.intercept() if contributions else None,
            "explanation": [
                {"feature": name, "contribution": value}
                for name, value in ranked[:_EXPLANATION_ITEMS]
            ],
        }

    def signals(self) -> dict[str, list[dict[str, Any]]]:
        """The latest prediction for every trained symbol, plus any that failed."""
        signals, errors = [], []
        for symbol in self.trained_symbols():
            try:
                _, bundle = self._loaded(symbol)
                p = Predictor(bundle).latest(self.bars(symbol), explain=False)
            except AiModelsError as exc:
                errors.append({"symbol": symbol, "error": str(exc)})
                continue
            signals.append(
                {
                    "symbol": symbol,
                    "date": p.date,
                    "prob_up": p.prob_up,
                    "signal_weight": p.signal_weight,
                    "forecast_vol": p.forecast_vol,
                    "regime": p.regime,
                    "anomaly_score": p.anomaly_score,
                    "is_anomaly": p.is_anomaly,
                }
            )
        return {"signals": signals, "errors": errors}

    def history(self, symbol: str, rows: int) -> dict[str, Any]:
        canonical = normalise_symbol(symbol)
        rows = max(1, min(rows, MAX_HISTORY_ROWS))
        metadata, bundle = self._loaded(canonical)
        bars = self.bars(canonical)
        rows = min(rows, len(compute_features(bars).dropna()))
        predictions = Predictor(bundle).predict(bars, rows=rows, explain=False)
        closes = bars["close"]
        points = []
        for p in predictions:
            points.append(
                {
                    "date": p.date,
                    "close": float(closes.loc[p.date]),
                    "prob_up": p.prob_up,
                    "signal_weight": p.signal_weight,
                    "forecast_vol": p.forecast_vol,
                    "regime": p.regime,
                    "anomaly_score": p.anomaly_score,
                    "is_anomaly": p.is_anomaly,
                }
            )
        return {
            "symbol": canonical,
            "model_version": metadata.version,
            "points": points,
        }

    def backtest(
        self,
        symbol: str,
        transaction_cost_bps: float,
        slippage_bps: float,
        initial_capital: float,
        target_volatility: float,
        long_threshold: float,
    ) -> dict[str, Any]:
        """Walk-forward backtest of the direction and volatility-target strategies."""
        canonical = normalise_symbol(symbol)
        bars = self.bars(canonical)
        try:
            metadata, bundle = self._loaded(canonical)
            config, version = bundle.config, metadata.version
        except AiModelsError:
            config, version = ModelConfig(), None  # the evaluation refits anyway

        key = (
            canonical,
            fingerprint(bars),
            config,
            transaction_cost_bps,
            slippage_bps,
            initial_capital,
            target_volatility,
            long_threshold,
        )
        with self._cache_lock:
            if key in self._backtests:
                self._backtests.move_to_end(key)
                return self._backtests[key]

        report = walk_forward_evaluate(
            bars,
            canonical,
            config,
            policy=SignalPolicy(long_threshold=long_threshold),
            cost=CostModel(transaction_cost_bps, slippage_bps),
            target_volatility=target_volatility,
            initial_capital=initial_capital,
        )
        result = {
            "symbol": canonical,
            "model_version": version,
            "evaluation": report.summary(),
            "direction": report.backtest.to_dict(),
            "volatility_target": report.volatility_backtest.to_dict(),
        }
        with self._cache_lock:
            self._backtests[key] = result
            while len(self._backtests) > _BACKTEST_CACHE_SIZE:
                self._backtests.popitem(last=False)
        return result

    # -------------------------------------------------------------- training
    def train(self, symbols: list[str] | None = None) -> list[dict[str, Any]]:
        """Train and store models for the given symbols (default: all with data)."""
        if not self.settings.allow_training:
            raise TrainingDisabledError("training through the API is disabled")
        available = self.data_symbols()
        wanted = [normalise_symbol(s) for s in symbols] if symbols else available
        missing = [s for s in wanted if s not in available]
        if missing:
            raise DataNotFoundError(f"no data file for symbol {missing[0]}")
        return self._train(wanted)

    def _train(self, symbols: list[str]) -> list[dict[str, Any]]:
        if not self._train_lock.acquire(blocking=False):
            raise TrainingBusyError("a training run is already in progress")
        try:
            trained = []
            for symbol in symbols:
                metadata, report = train_and_save(
                    self.registry, self.bars(symbol), symbol
                )
                with self._cache_lock:
                    self._bundles.pop(symbol, None)
                trained.append(
                    {
                        "symbol": symbol,
                        "version": metadata.version,
                        "rows": metadata.data.rows,
                        "evaluation": report.summary() if report is not None else {},
                    }
                )
                log.info("trained %s -> %s", symbol, metadata.version)
            return trained
        finally:
            self._train_lock.release()

    def startup(self) -> dict[str, list[str]]:
        """Train models that are missing or out of date. Never raises."""
        outcome: dict[str, list[str]] = {"trained": [], "failed": []}
        if not self.settings.auto_train:
            return outcome
        trained = set(self.trained_symbols())
        for symbol in self.data_symbols():
            try:
                if symbol in trained and not self.is_stale(symbol):
                    continue
                self._train([symbol])
                outcome["trained"].append(symbol)
            except AiModelsError as exc:
                log.warning("could not train %s: %s", symbol, exc)
                outcome["failed"].append(symbol)
        return outcome
