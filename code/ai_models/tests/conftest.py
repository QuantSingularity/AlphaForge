"""Shared fixtures for the ai_models tests."""

import sys
import warnings
from pathlib import Path

import pytest

# ai_models is imported as a top-level package, so the directory that contains it
# (code/) must be importable no matter where pytest is started.
CODE_DIR = Path(__file__).resolve().parents[2]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from ai_models.config import ModelConfig  # noqa: E402
from ai_models.pipeline import fit_bundle  # noqa: E402
from ai_models.registry import ModelRegistry  # noqa: E402
from ai_models.testing import synthetic_bars, write_csv  # noqa: E402


@pytest.fixture(scope="session")
def bars():
    """600 days of synthetic bars, shared (read-only) by the whole session."""
    return synthetic_bars(n=600, seed=1)


@pytest.fixture(scope="session")
def fitted_bundle(bars):
    """A bundle fitted once on the synthetic bars."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return fit_bundle(bars, "TEST", ModelConfig())


@pytest.fixture
def data_dir(tmp_path):
    """A data directory with three symbols in the engine's CSV format."""
    root = tmp_path / "data"
    for seed, symbol in enumerate(["AAA", "BBB", "CCC"], start=10):
        write_csv(synthetic_bars(n=500, seed=seed), root / f"{symbol}.csv")
    return root


@pytest.fixture
def registry(tmp_path):
    return ModelRegistry(tmp_path / "artifacts")
