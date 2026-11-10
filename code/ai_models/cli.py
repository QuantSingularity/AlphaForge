"""Command line interface: train, evaluate, list and predict.

python -m ai_models train --symbols AAPL SPY
python -m ai_models evaluate --symbols SPY --json
python -m ai_models list
python -m ai_models predict --symbol SPY
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from . import __version__
from .config import (
    DIRECTION_MODELS,
    ModelConfig,
    default_artifacts_dir,
    default_data_dir,
)
from .data import list_symbols, load_symbol, normalise_symbol
from .exceptions import AiModelsError
from .inference import Predictor
from .pipeline import EvaluationReport, train_and_save, walk_forward_evaluate
from .registry import ModelRegistry
from .signals import DEFAULT_TARGET_VOLATILITY


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--data-dir", type=Path, default=None, help="directory of <SYMBOL>.csv files"
    )
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        default=None,
        help="where model bundles are stored",
    )
    parser.add_argument(
        "--json", action="store_true", help="print machine-readable JSON"
    )


def _add_training(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=None,
        help="symbols to use (default: every CSV in the data directory)",
    )
    parser.add_argument("--model", choices=DIRECTION_MODELS, default="logistic")
    parser.add_argument(
        "--horizon", type=int, default=1, help="prediction horizon in trading days"
    )
    parser.add_argument(
        "--target-volatility",
        type=float,
        default=DEFAULT_TARGET_VOLATILITY,
        help="annualised volatility target of the volatility-sized strategy",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai_models", description="AlphaForge AI models"
    )
    parser.add_argument(
        "--version", action="version", version=f"ai_models {__version__}"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    train = commands.add_parser(
        "train", help="train models, evaluate walk-forward and store them"
    )
    _add_common(train)
    _add_training(train)
    train.add_argument(
        "--no-evaluate", action="store_true", help="skip the walk-forward evaluation"
    )

    evaluate = commands.add_parser(
        "evaluate", help="walk-forward evaluation only (nothing is stored)"
    )
    _add_common(evaluate)
    _add_training(evaluate)

    listing = commands.add_parser("list", help="list stored models")
    _add_common(listing)

    predict = commands.add_parser(
        "predict", help="latest prediction from a stored model"
    )
    _add_common(predict)
    predict.add_argument("--symbol", required=True)
    return parser


def _resolve_symbols(requested: list[str] | None, data_dir: Path) -> list[str]:
    if requested:
        return [normalise_symbol(s) for s in requested]
    symbols = list_symbols(data_dir)
    if not symbols:
        raise AiModelsError(f"no CSV files found in {data_dir}")
    return symbols


def _row(report: EvaluationReport) -> str:
    c = report.classification
    auc = "n/a" if c["auc"] is None else f"{c['auc']:.3f}"
    ic = (
        "n/a"
        if report.information_coefficient is None
        else f"{report.information_coefficient:+.3f}"
    )
    ml, vt, bh = (
        report.backtest.metrics,
        report.volatility_backtest.metrics,
        report.backtest.benchmark,
    )
    return (
        f"{report.symbol:<6}{report.folds:>6}{report.n_oos:>6}{c['accuracy']:>8.3f}{auc:>7}{ic:>8}"
        f"{ml.sharpe_ratio:>8.2f}{vt.sharpe_ratio:>8.2f}{vt.max_drawdown:>8.3f}"
        f"{bh.sharpe_ratio:>8.2f}{bh.max_drawdown:>8.3f}"
    )


_HEADER = (
    f"{'symbol':<6}{'folds':>6}{'oos':>6}{'acc':>8}{'auc':>7}{'ic':>8}"
    f"{'ml_sr':>8}{'vt_sr':>8}{'vt_dd':>8}{'bh_sr':>8}{'bh_dd':>8}"
)


def _print_reports(reports: list[EvaluationReport], as_json: bool) -> None:
    if as_json:
        print(json.dumps([r.summary() for r in reports], indent=2))
        return
    print(_HEADER)
    for report in reports:
        print(_row(report))
    print(
        "ml = direction model, vt = volatility target, bh = buy and hold (sr = Sharpe, dd = max drawdown)"
    )


def _model_config(args: argparse.Namespace) -> ModelConfig:
    return ModelConfig(direction_model=args.model, horizon=args.horizon)


def _command_train(args: argparse.Namespace) -> int:
    data_dir = args.data_dir or default_data_dir()
    registry = ModelRegistry(args.artifacts_dir or default_artifacts_dir())
    config = _model_config(args)
    reports: list[EvaluationReport] = []
    stored: list[dict[str, Any]] = []
    for symbol in _resolve_symbols(args.symbols, data_dir):
        bars = load_symbol(symbol, data_dir)
        metadata, report = train_and_save(
            registry,
            bars,
            symbol,
            config,
            evaluate=not args.no_evaluate,
            target_volatility=args.target_volatility,
        )
        stored.append(
            {"symbol": symbol, "version": metadata.version, "rows": metadata.data.rows}
        )
        if report is not None:
            reports.append(report)
        if not args.json:
            print(f"trained {symbol} -> {metadata.version}", file=sys.stderr)
    if reports:
        _print_reports(reports, args.json)
    elif args.json:
        print(json.dumps(stored, indent=2))
    return 0


def _command_evaluate(args: argparse.Namespace) -> int:
    data_dir = args.data_dir or default_data_dir()
    config = _model_config(args)
    reports = [
        walk_forward_evaluate(
            load_symbol(symbol, data_dir),
            symbol,
            config,
            target_volatility=args.target_volatility,
        )
        for symbol in _resolve_symbols(args.symbols, data_dir)
    ]
    _print_reports(reports, args.json)
    return 0


def _command_list(args: argparse.Namespace) -> int:
    registry = ModelRegistry(args.artifacts_dir or default_artifacts_dir())
    rows = []
    for symbol in registry.list_symbols():
        meta = registry.metadata(symbol)
        rows.append(
            {
                "symbol": symbol,
                "version": meta.version,
                "trained_at": meta.trained_at,
                "rows": meta.data.rows,
                "data_end": meta.data.end,
            }
        )
    if args.json:
        print(json.dumps(rows, indent=2))
    elif not rows:
        print("no trained models")
    else:
        for row in rows:
            print(
                f"{row['symbol']:<6} {row['version']}  rows={row['rows']}  data_end={row['data_end']}"
            )
    return 0


def _command_predict(args: argparse.Namespace) -> int:
    data_dir = args.data_dir or default_data_dir()
    registry = ModelRegistry(args.artifacts_dir or default_artifacts_dir())
    symbol = normalise_symbol(args.symbol)
    prediction = Predictor(registry.load(symbol)).latest(load_symbol(symbol, data_dir))
    payload = prediction.to_dict()
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        vol = (
            "n/a"
            if prediction.forecast_vol is None
            else f"{prediction.forecast_vol:.1%}"
        )
        print(
            f"{symbol} {prediction.date}: P(up)={prediction.prob_up:.1%} "
            f"weight={prediction.signal_weight:.2f} vol_forecast={vol} "
            f"regime={prediction.regime} anomaly={prediction.anomaly_score:.2f}"
        )
    return 0


_COMMANDS = {
    "train": _command_train,
    "evaluate": _command_evaluate,
    "list": _command_list,
    "predict": _command_predict,
}


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _COMMANDS[args.command](args)
    except AiModelsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
