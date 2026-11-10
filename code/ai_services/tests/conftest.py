"""Shared fixtures for the ai_services tests."""

import shutil
import sys
import warnings
from pathlib import Path

import pytest

# ai_services and ai_models are imported as top-level packages, so the directory
# that contains them (code/) must be importable no matter where pytest starts.
CODE_DIR = Path(__file__).resolve().parents[2]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from ai_models.testing import synthetic_bars, write_csv  # noqa: E402
from ai_services.app import create_app  # noqa: E402
from ai_services.service import AiService  # noqa: E402
from ai_services.settings import Settings  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

TRAINED = ("AAA", "BBB")
UNTRAINED = "CCC"


@pytest.fixture(scope="session")
def _seed_env(tmp_path_factory):
    """Data for three symbols; models trained for two of them. Built once."""
    root = tmp_path_factory.mktemp("seed")
    data, artifacts = root / "data", root / "artifacts"
    for seed, symbol in enumerate(["AAA", "BBB", "CCC"], start=20):
        write_csv(synthetic_bars(n=450, seed=seed), data / f"{symbol}.csv")
    settings = Settings(data_dir=data, artifacts_dir=artifacts, auto_train=False)
    service = AiService(settings)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        service.train(list(TRAINED))
    return data, artifacts


@pytest.fixture
def env(tmp_path, _seed_env):
    """A private copy of the seeded data and models, safe to modify."""
    data, artifacts = tmp_path / "data", tmp_path / "artifacts"
    shutil.copytree(_seed_env[0], data)
    shutil.copytree(_seed_env[1], artifacts)
    return data, artifacts


@pytest.fixture
def settings(env):
    return Settings(data_dir=env[0], artifacts_dir=env[1], auto_train=False)


@pytest.fixture
def service(settings):
    return AiService(settings)


@pytest.fixture
def client(settings, service):
    with TestClient(
        create_app(settings, service), raise_server_exceptions=False
    ) as test_client:
        yield test_client
