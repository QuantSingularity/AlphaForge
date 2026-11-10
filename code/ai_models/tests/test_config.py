import os

import pytest
from ai_models import config
from ai_models.config import CostModel, ModelConfig, WalkForwardConfig


class TestCostModel:
    def test_defaults_match_the_engine(self):
        cost = CostModel()

        assert cost.transaction_cost_bps == 5.0
        assert cost.slippage_bps == 2.0
        assert cost.rate == pytest.approx(0.0007)

    def test_rate_adds_both_components(self):
        assert CostModel(10.0, 5.0).rate == pytest.approx(0.0015)

    def test_zero_cost_is_allowed(self):
        assert CostModel(0.0, 0.0).rate == 0.0

    @pytest.mark.parametrize("args", [(-1.0, 0.0), (0.0, -0.5)])
    def test_negative_costs_are_rejected(self, args):
        with pytest.raises(ValueError, match="non-negative"):
            CostModel(*args)


class TestModelConfig:
    def test_defaults_are_valid(self):
        cfg = ModelConfig()

        assert cfg.horizon == 1
        assert cfg.direction_model == "logistic"

    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"horizon": 0}, "horizon"),
            ({"direction_model": "svm"}, "direction_model"),
            ({"logistic_c": 0.0}, "logistic_c"),
            ({"regime_components": 5}, "regime_components"),
            ({"anomaly_contamination": 0.0}, "anomaly_contamination"),
            ({"anomaly_contamination": 0.5}, "anomaly_contamination"),
            ({"volatility_alpha": -1.0}, "volatility_alpha"),
        ],
    )
    def test_invalid_values_are_rejected(self, kwargs, message):
        with pytest.raises(ValueError, match=message):
            ModelConfig(**kwargs)

    def test_is_immutable(self):
        with pytest.raises(AttributeError):
            ModelConfig().horizon = 5


class TestWalkForwardConfig:
    def test_defaults_are_valid(self):
        cfg = WalkForwardConfig()

        assert (cfg.min_train, cfg.test_size, cfg.step) == (252, 21, 21)
        assert cfg.embargo is None

    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"min_train": 10}, "min_train"),
            ({"test_size": 0}, "positive"),
            ({"step": 0}, "positive"),
            ({"embargo": -1}, "embargo"),
            ({"test_size": 21, "step": 10}, "overlap"),
        ],
    )
    def test_invalid_values_are_rejected(self, kwargs, message):
        with pytest.raises(ValueError, match=message):
            WalkForwardConfig(**kwargs)


class TestPaths:
    def test_repo_root_contains_the_code_directory(self):
        assert (config.repo_root() / "code").is_dir()

    def test_data_dir_defaults_to_the_engine_data_directory(self, monkeypatch):
        monkeypatch.delenv(config.DATA_DIR_ENV, raising=False)

        assert (
            config.default_data_dir()
            == config.repo_root() / "code" / "backend" / "data"
        )

    def test_data_dir_can_be_overridden_by_the_environment(self, monkeypatch, tmp_path):
        monkeypatch.setenv(config.DATA_DIR_ENV, str(tmp_path))

        assert config.default_data_dir() == tmp_path

    def test_artifacts_dir_defaults_inside_ai_models(self, monkeypatch):
        monkeypatch.delenv(config.ARTIFACTS_DIR_ENV, raising=False)

        assert config.default_artifacts_dir() == (
            config.repo_root() / "code" / "ai_models" / "artifacts"
        )

    def test_artifacts_dir_can_be_overridden_by_the_environment(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setenv(config.ARTIFACTS_DIR_ENV, str(tmp_path))

        assert config.default_artifacts_dir() == tmp_path

    def test_environment_is_untouched_by_the_tests(self):
        assert os.environ.get(config.DATA_DIR_ENV) in (None, "")
