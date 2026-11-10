"""Statistical and behavioural validation of the ai_models pipeline.

The unit tests prove that each function does what it says. This script asks a
different question: can the trained pipeline be trusted? It runs the real
training and evaluation code on synthetic data whose truth is known, and checks
the results against that truth:

  direction   no skill on pure noise (so no look-ahead leak), real skill when a
              signal is planted, and a deliberate leak that the leak check catches
  volatility  the HAR forecast beats the naive historical baseline
  regime      calm and turbulent periods are told apart
  anomaly     a planted crash is flagged, and the flag rate matches the setting
  pipeline    walk-forward folds never leak, future bars never change a past
              prediction, results are deterministic, stored models reload
              exactly and a corrupted file is rejected, outputs are valid and
              higher trading costs never raise the final equity

Every check records what was measured and what was required, so a failure says
why. Thresholds sit well clear of the values measured on the pinned seeds, so a
healthy pipeline passes on every supported Python and library version while a
real defect (a leak, a broken forecaster) fails by a wide margin.

    python scripts/validate_ai_models.py
    python scripts/validate_ai_models.py --report validation-report.json

Needs the ai_models dependencies and no network or market data. Writes a
Markdown table to $GITHUB_STEP_SUMMARY when that variable is set (GitHub
Actions does this). Exits 1 if any check fails.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))

from ai_models.backtest import run_backtest  # noqa: E402
from ai_models.config import CostModel, ModelConfig, WalkForwardConfig  # noqa: E402
from ai_models.exceptions import ArtifactError  # noqa: E402
from ai_models.features import compute_features, make_dataset  # noqa: E402
from ai_models.inference import Predictor  # noqa: E402
from ai_models.models import DirectionModel, RegimeDetector  # noqa: E402
from ai_models.pipeline import (  # noqa: E402
    fit_bundle,
    train_and_save,
    walk_forward_evaluate,
)
from ai_models.registry import ModelRegistry  # noqa: E402
from ai_models.testing import synthetic_bars  # noqa: E402
from ai_models.evaluation import walk_forward_splits  # noqa: E402

# ---------------------------------------------------------------- thresholds
# Measured on the pinned seeds: null AUC 0.506 (range 0.46-0.54 across seeds),
# planted-signal AUC 0.60, leaked AUC ~1.0, HAR beats historical QLIKE on 6/6.
NULL_SEEDS = range(100, 108)
NULL_AUC_BAND = (0.46, 0.54)  # mean OOS AUC on signal-free data
NULL_AUC_MAX_SINGLE = 0.60  # no single signal-free series may look skilful
NULL_IC_MAX = 0.06  # mean |rank IC| on signal-free data
LEAK_AUC_MIN = 0.90  # the canary must be caught at least this clearly
PLANTED_PHI = 0.3  # AR(1) coefficient of the planted momentum signal
PLANTED_AUC_MIN_MEAN = 0.55
PLANTED_AUC_MIN_EACH = 0.53
VOL_SEEDS = range(300, 306)
VOL_MIN_WINS = 5  # of 6 series on which HAR must beat the historical baseline
VOL_EWMA_TOLERANCE = 1.05  # HAR QLIKE may be at most 5% worse than EWMA
REGIME_DOMINANCE_MIN = 0.90  # calm days assigned to their dominant regime
REGIME_LEAKAGE_MAX = 0.10  # turbulent days assigned to the calm regime
ANOMALY_RATE_BAND = (0.5, 2.0)  # observed flag rate as a multiple of the setting


# ------------------------------------------------------------------ plumbing
@dataclass
class Result:
    group: str
    name: str
    passed: bool
    measured: str
    required: str


class Validation:
    """Collects results; a check that raises is a failure, not a crash."""

    def __init__(self) -> None:
        self.results: list[Result] = []

    def record(
        self, group: str, name: str, passed: bool, measured: str, required: str
    ) -> None:
        self.results.append(Result(group, name, bool(passed), measured, required))
        mark = "ok  " if passed else "FAIL"
        print(f"  {mark} {name}: {measured} (required: {required})")

    def run(self, group: str, check: Callable[[Validation], None]) -> None:
        print(f"\n[{group}] {check.__name__}")
        started = time.monotonic()
        try:
            check(self)
        except Exception as exc:  # a broken check must not hide the others
            self.record(group, check.__name__, False, f"raised {exc!r}", "no error")
        print(f"  ({time.monotonic() - started:.1f}s)")

    @property
    def failures(self) -> list[Result]:
        return [r for r in self.results if not r.passed]


# --------------------------------------------------------- synthetic fixtures
def bars_from_returns(returns: np.ndarray, seed: int) -> pd.DataFrame:
    """Valid daily OHLCV bars whose close-to-close log returns are `returns`."""
    rng = np.random.default_rng(seed)
    n = len(returns)
    close = 100.0 * np.exp(np.cumsum(returns))
    open_ = np.concatenate(([100.0], close[:-1]))
    spread = np.abs(rng.normal(0.0, 0.004, n)) + 0.0005
    return pd.DataFrame(
        {
            "open": open_,
            "high": np.maximum(open_, close) * (1 + spread),
            "low": np.minimum(open_, close) * (1 - spread),
            "close": close,
            "volume": np.round(1e6 * np.exp(rng.normal(0.0, 0.25, n))),
        },
        index=pd.bdate_range("2018-01-01", periods=n, name="date"),
    )


def planted_momentum_bars(n: int, seed: int, phi: float) -> pd.DataFrame:
    """Returns follow r[t] = phi * r[t-1] + noise: yesterday predicts today."""
    rng = np.random.default_rng(seed)
    noise = rng.normal(0.0, 0.01, n)
    returns = np.zeros(n)
    for i in range(1, n):
        returns[i] = phi * returns[i - 1] + noise[i]
    return bars_from_returns(returns, seed)


def oos_auc(features: pd.DataFrame, target: pd.Series, config: ModelConfig) -> float:
    """Walk-forward out-of-sample AUC of the direction model on given features."""
    splits = walk_forward_splits(len(features), WalkForwardConfig(), config.horizon)
    probabilities, outcomes = [], []
    for split in splits:
        model = DirectionModel(config).fit(
            features.iloc[: split.train_end], target.iloc[: split.train_end]
        )
        test = slice(split.test_start, split.test_end)
        probabilities.append(model.predict_proba_up(features.iloc[test]))
        outcomes.append(target.iloc[test].to_numpy())
    return float(roc_auc_score(np.concatenate(outcomes), np.concatenate(probabilities)))


def fmt(values: Any, spec: str = ".3f") -> str:
    return "[" + ", ".join(format(float(v), spec) for v in values) + "]"


# ------------------------------------------------------------------ direction
def direction_no_skill_on_noise(v: Validation) -> None:
    """With no signal to find, out-of-sample skill must be nil. Skill here means
    a look-ahead leak: something in the features or folds sees the answer."""
    aucs, ics = [], []
    for seed in NULL_SEEDS:
        report = walk_forward_evaluate(synthetic_bars(n=900, seed=seed), "NULL")
        aucs.append(report.classification["auc"])
        ics.append(report.information_coefficient)
    mean_auc, lo, hi = float(np.mean(aucs)), *NULL_AUC_BAND
    v.record(
        "direction",
        "mean AUC on signal-free data",
        lo <= mean_auc <= hi,
        f"{mean_auc:.3f} over {len(aucs)} series",
        f"{lo} to {hi}",
    )
    v.record(
        "direction",
        "no signal-free series looks skilful",
        max(aucs) < NULL_AUC_MAX_SINGLE,
        f"max AUC {max(aucs):.3f}",
        f"below {NULL_AUC_MAX_SINGLE}",
    )
    mean_ic = abs(float(np.mean(ics)))
    v.record(
        "direction",
        "mean rank IC on signal-free data",
        mean_ic < NULL_IC_MAX,
        f"mean IC {mean_ic:.3f} (absolute)",
        f"below {NULL_IC_MAX}",
    )


def direction_leak_check_has_teeth(v: Validation) -> None:
    """The no-skill check is only worth something if it fails when there is a
    leak. Feed the model its own answer as a feature and require the same
    procedure to report near-perfect skill."""
    config = ModelConfig()
    bars = synthetic_bars(n=900, seed=NULL_SEEDS[0])
    dataset = make_dataset(bars, config.horizon)
    clean = oos_auc(dataset.features, dataset.target, config)
    leaked_features = dataset.features.assign(leak=dataset.forward_return)
    leaked = oos_auc(leaked_features, dataset.target, config)
    v.record(
        "direction",
        "same series without the leak",
        NULL_AUC_BAND[0] <= clean <= NULL_AUC_MAX_SINGLE,
        f"AUC {clean:.3f}",
        f"{NULL_AUC_BAND[0]} to {NULL_AUC_MAX_SINGLE}",
    )
    v.record(
        "direction",
        "deliberate leak is detected",
        leaked >= LEAK_AUC_MIN,
        f"AUC {leaked:.3f}",
        f"at least {LEAK_AUC_MIN}",
    )


def direction_finds_planted_signal(v: Validation) -> None:
    """When momentum really exists, the pipeline must find it out of sample."""
    aucs = []
    for seed in range(200, 204):
        bars = planted_momentum_bars(1200, seed, PLANTED_PHI)
        aucs.append(walk_forward_evaluate(bars, "PLANTED").classification["auc"])
    v.record(
        "direction",
        "mean AUC with planted momentum",
        float(np.mean(aucs)) >= PLANTED_AUC_MIN_MEAN,
        f"{np.mean(aucs):.3f} {fmt(aucs)}",
        f"at least {PLANTED_AUC_MIN_MEAN}",
    )
    v.record(
        "direction",
        "every planted-signal series is detected",
        min(aucs) >= PLANTED_AUC_MIN_EACH,
        f"min AUC {min(aucs):.3f}",
        f"at least {PLANTED_AUC_MIN_EACH}",
    )


# ----------------------------------------------------------------- volatility
def volatility_beats_naive_baseline(v: Validation) -> None:
    """QLIKE is the standard loss for variance forecasts (lower is better)."""
    qlike: dict[str, list[float]] = {"har": [], "ewma": [], "historical": []}
    for seed in VOL_SEEDS:
        volatility = walk_forward_evaluate(
            synthetic_bars(n=1200, seed=seed), "VOL"
        ).volatility
        for name in qlike:
            qlike[name].append(volatility[name]["qlike"])
    wins = sum(h < b for h, b in zip(qlike["har"], qlike["historical"]))
    v.record(
        "volatility",
        "HAR beats historical volatility",
        wins >= VOL_MIN_WINS and np.mean(qlike["har"]) < np.mean(qlike["historical"]),
        f"{wins}/{len(VOL_SEEDS)} series, mean QLIKE "
        f"{np.mean(qlike['har']):.3f} vs {np.mean(qlike['historical']):.3f}",
        f"at least {VOL_MIN_WINS} wins and a lower mean",
    )
    v.record(
        "volatility",
        "HAR competitive with EWMA",
        np.mean(qlike["har"]) <= VOL_EWMA_TOLERANCE * np.mean(qlike["ewma"]),
        f"mean QLIKE {np.mean(qlike['har']):.3f} vs {np.mean(qlike['ewma']):.3f}",
        f"within {VOL_EWMA_TOLERANCE - 1:.0%} of EWMA",
    )


# --------------------------------------------------------------------- regime
def regime_separates_calm_from_turbulent(v: Validation) -> None:
    rng = np.random.default_rng(5)
    returns = np.concatenate(
        [rng.normal(0.0006, 0.005, 400), rng.normal(-0.002, 0.03, 400)]
    )
    features = compute_features(bars_from_returns(returns, 5)).dropna()
    labels = pd.Series(RegimeDetector(ModelConfig()).fit(features).predict(features))
    calm, turbulent = labels.iloc[:300], labels.iloc[-300:]
    dominant = calm.value_counts().index[0]
    dominance = float((calm == dominant).mean())
    leakage = float((turbulent == dominant).mean())
    v.record(
        "regime",
        "calm period gets one consistent regime",
        dominance >= REGIME_DOMINANCE_MIN,
        f"{dominance:.0%} labelled {dominant!r}",
        f"at least {REGIME_DOMINANCE_MIN:.0%}",
    )
    v.record(
        "regime",
        "turbulent period is not labelled calm",
        leakage <= REGIME_LEAKAGE_MAX,
        f"{leakage:.0%} labelled {dominant!r}",
        f"at most {REGIME_LEAKAGE_MAX:.0%}",
    )


# -------------------------------------------------------------------- anomaly
def crash_bar(bars: pd.DataFrame) -> pd.DataFrame:
    """The next trading day: a 25% crash on ten times the usual volume."""
    last = bars.iloc[-1]
    close = float(last["close"])
    return pd.DataFrame(
        {
            "open": [close],
            "high": [close * 1.002],
            "low": [close * 0.74],
            "close": [close * 0.75],
            "volume": [float(last["volume"]) * 10],
        },
        index=[bars.index[-1] + pd.offsets.BDay(1)],
    )


def anomaly_flags_a_planted_crash(v: Validation) -> None:
    bars = synthetic_bars(n=700, seed=7)
    config = ModelConfig()
    predictor = Predictor(fit_bundle(bars, "ANOM", config))

    history = predictor.predict(bars, rows=len(bars) - 70)
    rate = float(np.mean([p.is_anomaly for p in history]))
    low, high = (m * config.anomaly_contamination for m in ANOMALY_RATE_BAND)
    v.record(
        "anomaly",
        "flag rate matches the configured contamination",
        low <= rate <= high,
        f"{rate:.3f} of {len(history)} days",
        f"{low:.3f} to {high:.3f} (setting {config.anomaly_contamination})",
    )
    calm = predictor.predict(bars, rows=1)[0]
    crash = predictor.predict(pd.concat([bars, crash_bar(bars)]), rows=1)[0]
    v.record(
        "anomaly",
        "an ordinary day is not flagged",
        not calm.is_anomaly,
        f"score {calm.anomaly_score:.3f}",
        "not flagged",
    )
    v.record(
        "anomaly",
        "a 25% crash on 10x volume is flagged",
        crash.is_anomaly,
        f"score {crash.anomaly_score:.3f}",
        "flagged",
    )


# ------------------------------------------------------------------- pipeline
def pipeline_walk_forward_folds_do_not_leak(v: Validation) -> None:
    """Whatever the horizon and embargo, no training label may reach the test
    window, folds must not overlap, and training only ever grows."""
    problems: list[str] = []
    for horizon in (1, 5, 21):
        for extra in (0, 3):
            wf = WalkForwardConfig(embargo=horizon + extra)
            splits = walk_forward_splits(1500, wf, horizon)
            if not splits:
                problems.append(f"h={horizon}+{extra}: no folds")
            for a, b in zip(splits, splits[1:]):
                if b.test_start < a.test_end or b.train_end < a.train_end:
                    problems.append(f"h={horizon}+{extra}: folds overlap or shrink")
            for s in splits:
                label_end = s.train_end - 1 + horizon  # last row a label reaches
                if label_end >= s.test_start:
                    problems.append(f"h={horizon}+{extra}: label reaches test")
                if s.test_start - s.train_end != horizon + extra:
                    problems.append(f"h={horizon}+{extra}: wrong embargo")
    try:
        walk_forward_splits(1500, WalkForwardConfig(embargo=1), horizon=5)
        rejects = False
    except ValueError:
        rejects = True
    v.record(
        "pipeline",
        "folds are leak-free for horizons 1, 5, 21",
        not problems,
        "no problems" if not problems else "; ".join(sorted(set(problems))[:3]),
        "no problems",
    )
    v.record(
        "pipeline",
        "an embargo shorter than the horizon is rejected",
        rejects,
        "rejected" if rejects else "accepted",
        "rejected",
    )


def pipeline_future_bars_never_change_a_past_prediction(v: Validation) -> None:
    """Rewrite everything after day t. Predictions for day t must not move by a
    single bit, for any of the four models."""
    bars = synthetic_bars(n=700, seed=7)
    predictor = Predictor(fit_bundle(bars.iloc[:450], "CAUSAL"))
    price_columns = [bars.columns.get_loc(c) for c in ("open", "high", "low", "close")]
    volume_column = bars.columns.get_loc("volume")
    drift: list[str] = []
    for position in (500, 600):
        edited = bars.copy()
        edited.iloc[position + 1 :, price_columns] *= 1.7
        edited.iloc[position + 1 :, volume_column] *= 4
        rows = len(bars) - position
        date = bars.index[position].strftime("%Y-%m-%d")
        before = {p.date: p for p in predictor.predict(bars, rows=rows)}[date]
        after = {p.date: p for p in predictor.predict(edited, rows=rows)}[date]
        for field in ("prob_up", "forecast_vol", "regime", "anomaly_score"):
            if getattr(before, field) != getattr(after, field):
                drift.append(f"{field}@{position}")
    v.record(
        "pipeline",
        "future bars do not change past predictions",
        not drift,
        "identical" if not drift else "changed: " + ", ".join(drift),
        "identical",
    )


def pipeline_is_deterministic(v: Validation) -> None:
    bars = synthetic_bars(n=700, seed=7)

    def predictions() -> list[dict[str, object]]:
        bundle = fit_bundle(bars, "DET")
        return [p.to_dict() for p in Predictor(bundle).predict(bars, rows=100)]

    def evaluation() -> str:
        return json.dumps(walk_forward_evaluate(bars, "DET").summary(), sort_keys=True)

    same_predictions = predictions() == predictions()
    same_evaluation = evaluation() == evaluation()
    v.record(
        "pipeline",
        "retraining on the same data gives identical predictions",
        same_predictions,
        "identical" if same_predictions else "differ",
        "identical",
    )
    v.record(
        "pipeline",
        "re-running the evaluation gives an identical report",
        same_evaluation,
        "identical" if same_evaluation else "differ",
        "identical",
    )


def pipeline_stored_models_reload_exactly(v: Validation) -> None:
    bars = synthetic_bars(n=700, seed=7)
    with tempfile.TemporaryDirectory(prefix="ai-models-validate-") as tmp:
        registry = ModelRegistry(tmp)
        metadata, _ = train_and_save(registry, bars, "STORE", evaluate=False)
        expected = [
            p.to_dict()
            for p in Predictor(fit_bundle(bars, "STORE")).predict(bars, rows=100)
        ]
        reloaded = [
            p.to_dict()
            for p in Predictor(ModelRegistry(tmp).load("STORE")).predict(bars, rows=100)
        ]
        v.record(
            "pipeline",
            "a stored model predicts exactly like the one trained",
            reloaded == expected,
            "identical" if reloaded == expected else "differ",
            "identical",
        )

        path = Path(tmp) / "STORE" / metadata.version / "bundle.joblib"
        raw = bytearray(path.read_bytes())
        raw[len(raw) // 2] ^= 0xFF
        path.write_bytes(bytes(raw))
        # The checksum must be what stops it, not luck in how the file fails to parse.
        try:
            ModelRegistry(tmp).load("STORE")
            outcome = "loaded a corrupted file"
        except ArtifactError as exc:
            outcome = (
                "rejected by checksum"
                if "checksum" in str(exc)
                else f"failed without a checksum error: {exc}"
            )
        v.record(
            "pipeline",
            "a corrupted model file is rejected by its checksum",
            outcome == "rejected by checksum",
            outcome,
            "rejected by checksum",
        )


def pipeline_outputs_are_valid(v: Validation) -> None:
    bars = synthetic_bars(n=700, seed=7)
    predictions = Predictor(fit_bundle(bars, "OUT")).predict(bars, rows=630)
    prob = np.array([p.prob_up for p in predictions])
    weight = np.array([p.signal_weight for p in predictions])
    vol = np.array(
        [np.nan if p.forecast_vol is None else p.forecast_vol for p in predictions]
    )
    regime_sum = np.array([sum(p.regime_probabilities.values()) for p in predictions])
    v.record(
        "pipeline",
        "probabilities are finite and within [0, 1]",
        bool(np.isfinite(prob).all() and ((prob >= 0) & (prob <= 1)).all()),
        f"range {prob.min():.3f} to {prob.max():.3f} over {len(prob)} days",
        "finite, within [0, 1]",
    )
    v.record(
        "pipeline",
        "position weights are within [0, 1]",
        bool(((weight >= 0) & (weight <= 1)).all()),
        f"range {weight.min():.2f} to {weight.max():.2f}",
        "within [0, 1] (long-only, unlevered)",
    )
    v.record(
        "pipeline",
        "volatility forecasts are finite and positive",
        bool(np.isfinite(vol).all() and (vol > 0).all()),
        f"min {np.nanmin(vol):.3f}",
        "finite and above 0",
    )
    v.record(
        "pipeline",
        "regime probabilities sum to 1",
        bool(np.allclose(regime_sum, 1.0, atol=1e-9)),
        f"max error {np.abs(regime_sum - 1.0).max():.1e}",
        "within 1e-9",
    )
    try:
        json.dumps(walk_forward_evaluate(bars, "OUT").summary(), allow_nan=False)
        valid_json = True
    except ValueError:
        valid_json = False
    v.record(
        "pipeline",
        "the evaluation report is valid JSON (no NaN or Infinity)",
        valid_json,
        "valid" if valid_json else "contains NaN or Infinity",
        "valid",
    )


def pipeline_costs_never_help(v: Validation) -> None:
    bars = synthetic_bars(n=700, seed=7)
    oos = walk_forward_evaluate(bars, "COST").predictions
    closes = bars["close"].reindex(oos.index).to_numpy()
    dates = [d.strftime("%Y-%m-%d") for d in oos.index]
    weights = oos["weight"].to_numpy()

    def final_equity(w: np.ndarray, cost: CostModel) -> float:
        return float(run_backtest("COST", "COST", dates, closes, w, cost).equity[-1])

    free = final_equity(weights, CostModel(0.0, 0.0))
    normal = final_equity(weights, CostModel())
    heavy = final_equity(weights, CostModel(50.0, 20.0))
    v.record(
        "pipeline",
        "higher costs never raise the final equity",
        free >= normal >= heavy,
        f"{free:,.0f} (no cost) >= {normal:,.0f} (default) >= {heavy:,.0f} (heavy)",
        "non-increasing in cost",
    )
    idle = run_backtest(
        "IDLE", "COST", dates, closes, np.zeros_like(weights), CostModel()
    ).equity
    v.record(
        "pipeline",
        "holding no position leaves capital untouched",
        bool(np.allclose(idle, idle[0])),
        f"equity {idle.min():,.2f} to {idle.max():,.2f}",
        "constant",
    )


CHECKS: list[tuple[str, Callable[[Validation], None]]] = [
    ("direction", direction_no_skill_on_noise),
    ("direction", direction_leak_check_has_teeth),
    ("direction", direction_finds_planted_signal),
    ("volatility", volatility_beats_naive_baseline),
    ("regime", regime_separates_calm_from_turbulent),
    ("anomaly", anomaly_flags_a_planted_crash),
    ("pipeline", pipeline_walk_forward_folds_do_not_leak),
    ("pipeline", pipeline_future_bars_never_change_a_past_prediction),
    ("pipeline", pipeline_is_deterministic),
    ("pipeline", pipeline_stored_models_reload_exactly),
    ("pipeline", pipeline_outputs_are_valid),
    ("pipeline", pipeline_costs_never_help),
]


# -------------------------------------------------------------------- reports
def escape_cell(text: str) -> str:
    """Keep a pipe or newline in a value from breaking a Markdown table row."""
    return text.replace("|", "\\|").replace("\n", " ")


def markdown_summary(validation: Validation) -> str:
    failed = len(validation.failures)
    total = len(validation.results)
    headline = (
        f"All {total} validation checks passed."
        if not failed
        else f"{failed} of {total} validation checks failed."
    )
    lines = [
        f"### ai_models validation (Python {sys.version_info.major}.{sys.version_info.minor})",
        "",
        headline,
        "",
        "| | Group | Check | Measured | Required |",
        "| :-: | :-- | :-- | :-- | :-- |",
    ]
    for r in validation.results:
        cells = [escape_cell(c) for c in (r.group, r.name, r.measured, r.required)]
        lines.append(f"| {'✅' if r.passed else '❌'} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--report", type=Path, help="write a JSON report to this path")
    args = parser.parse_args(argv)

    validation = Validation()
    started = time.monotonic()
    for group, check in CHECKS:
        validation.run(group, check)

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(markdown_summary(validation))
    if args.report:
        args.report.write_text(
            json.dumps(
                {
                    "python": sys.version.split()[0],
                    "passed": not validation.failures,
                    "checks": [asdict(r) for r in validation.results],
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    elapsed = time.monotonic() - started
    total = len(validation.results)
    if validation.failures:
        print(
            f"\n{len(validation.failures)} of {total} checks FAILED ({elapsed:.0f}s):"
        )
        for r in validation.failures:
            print(f"  - [{r.group}] {r.name}: {r.measured} (required: {r.required})")
        return 1
    print(f"\nAll {total} validation checks passed ({elapsed:.0f}s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
