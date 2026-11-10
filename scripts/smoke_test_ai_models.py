"""End-to-end smoke test of the ai_models command line.

Runs the real CLI (python -m ai_models) in separate processes on synthetic bars,
so it covers what the unit tests cannot: the package entry point, models written
to disk by one process and loaded by another, and the --json output contract.

    python scripts/smoke_test_ai_models.py

Needs the ai_models dependencies (numpy, pandas, scikit-learn, joblib) and no
network or market data. Exits 1 if any step fails.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
SYMBOLS = ["AAA", "BBB"]

sys.path.insert(0, str(CODE_DIR))

from ai_models.testing import synthetic_bars, write_csv  # noqa: E402


def run_cli(env: dict[str, str], *args: str) -> str:
    """Run `python -m ai_models ...`, return stdout, fail loudly on a non-zero exit."""
    command = [sys.executable, "-m", "ai_models", *args]
    print(f"$ python -m ai_models {' '.join(args)}")
    result = subprocess.run(command, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        sys.stderr.write(result.stdout + result.stderr)
        raise SystemExit(f"command failed with exit code {result.returncode}")
    return result.stdout


def require(condition: bool, message: str, detail: Any = None) -> None:
    if not condition:
        raise SystemExit(
            f"check failed: {message}" + (f" ({detail})" if detail else "")
        )
    print(f"  ok  {message}")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="ai-models-smoke-") as tmp:
        data_dir, model_dir = Path(tmp) / "data", Path(tmp) / "models"
        for seed, symbol in enumerate(SYMBOLS, start=30):
            write_csv(synthetic_bars(n=500, seed=seed), data_dir / f"{symbol}.csv")

        env = {
            **os.environ,
            "PYTHONPATH": str(CODE_DIR),
            "ALPHAFORGE_DATA_DIR": str(data_dir),
            "ALPHAFORGE_MODEL_DIR": str(model_dir),
        }

        version = run_cli(env, "--version")
        require(
            version.startswith("ai_models "), "--version names the package", version
        )

        # Train and evaluate (walk-forward) every symbol, writing bundles to disk.
        trained = json.loads(run_cli(env, "train", "--json"))
        require(
            sorted(r["symbol"] for r in trained) == SYMBOLS,
            "train reports a report per symbol",
        )
        require(
            all(r["folds"] > 0 for r in trained), "walk-forward ran at least a fold"
        )

        # A fresh process must be able to load what the previous one stored.
        listed = json.loads(run_cli(env, "list", "--json"))
        require(
            sorted(r["symbol"] for r in listed) == SYMBOLS,
            "list sees the stored models",
        )

        for symbol in SYMBOLS:
            prediction = json.loads(
                run_cli(env, "predict", "--symbol", symbol, "--json")
            )
            prob_up = prediction["prob_up"]
            require(
                0.0 <= prob_up <= 1.0 and math.isfinite(prob_up),
                f"{symbol}: prob_up is a probability",
                prob_up,
            )
            require(bool(prediction["regime"]), f"{symbol}: has a regime")

        # Evaluation alone must not store anything new.
        before = sorted(p.name for p in model_dir.rglob("*"))
        run_cli(env, "evaluate", "--symbols", SYMBOLS[0], "--json")
        require(
            before == sorted(p.name for p in model_dir.rglob("*")),
            "evaluate stores nothing",
        )

        # Errors are reported on stderr with exit code 1, not a traceback.
        failed = subprocess.run(
            [sys.executable, "-m", "ai_models", "predict", "--symbol", "NOSUCH"],
            env=env,
            capture_output=True,
            text=True,
        )
        require(
            failed.returncode == 1 and failed.stderr.startswith("error:"),
            "an unknown symbol exits 1 with an error message",
            failed.stderr.strip(),
        )

    print("\nAll ai_models CLI checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
