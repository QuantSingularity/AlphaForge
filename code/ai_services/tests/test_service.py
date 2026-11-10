import threading
import time

import pytest
from ai_models.config import WalkForwardConfig
from ai_models.exceptions import (
    ArtifactError,
    DataError,
    DataNotFoundError,
    InsufficientDataError,
    ModelNotFoundError,
)
from ai_models.testing import synthetic_bars, write_csv
from ai_services import service as service_module
from ai_services.service import (
    MAX_HISTORY_ROWS,
    AiService,
    TrainingBusyError,
    TrainingDisabledError,
)
from ai_services.settings import Settings

BACKTEST = dict(
    transaction_cost_bps=5.0,
    slippage_bps=2.0,
    initial_capital=1_000_000.0,
    target_volatility=0.12,
    long_threshold=0.55,
)


class TestDataAndCaching:
    def test_lists_the_symbols_with_data(self, service):
        assert service.data_symbols() == ["AAA", "BBB", "CCC"]

    def test_bars_are_cached_until_the_file_changes(self, service, env):
        first = service.bars("aaa")

        assert service.bars("AAA") is first

        write_csv(synthetic_bars(n=450, seed=99), env[0] / "AAA.csv")
        assert service.bars("AAA") is not first

    def test_unknown_symbols_have_no_bars(self, service):
        with pytest.raises(DataNotFoundError):
            service.bars("ZZZ")

    def test_bundles_are_loaded_once_per_version(self, service):
        first = service._loaded("AAA")

        assert service._loaded("AAA") is first

    def test_a_new_version_replaces_the_cached_bundle(self, service):
        before = service._loaded("AAA")[0].version

        service.train(["AAA"])

        assert service._loaded("AAA")[0].version != before

    def test_an_untrained_symbol_has_no_bundle(self, service):
        with pytest.raises(ModelNotFoundError):
            service._loaded("CCC")


class TestStaleness:
    def test_a_fresh_model_is_not_stale(self, service):
        assert service.is_stale("AAA") is False

    def test_changed_data_makes_a_model_stale(self, service, env):
        write_csv(synthetic_bars(n=450, seed=99), env[0] / "AAA.csv")

        assert service.is_stale("AAA") is True

    def test_a_model_whose_data_vanished_is_not_reported_stale(self, service, env):
        (env[0] / "AAA.csv").unlink()

        assert service.is_stale("AAA") is False


class TestQueries:
    def test_health_counts_models_and_data(self, service):
        health = service.health()

        assert health["status"] == "ok"
        assert (health["models"], health["data_symbols"]) == (2, 3)
        assert health["training_enabled"] is True
        assert health["auto_train"] is False

    def test_models_describe_what_was_trained(self, service):
        models = {m["symbol"]: m for m in service.models()}

        assert set(models) == {"AAA", "BBB"}
        assert models["AAA"]["data"]["rows"] == 450
        assert models["AAA"]["stale"] is False
        assert "classification" in models["AAA"]["evaluation"]

    def test_predict_has_a_ranked_explanation(self, service):
        result = service.predict("aaa")

        assert result["symbol"] == "AAA"
        assert 0 < result["prob_up"] < 1
        explanation = result["explanation"]
        assert 0 < len(explanation) <= 8
        magnitudes = [abs(item["contribution"]) for item in explanation]
        assert magnitudes == sorted(magnitudes, reverse=True)
        assert isinstance(result["intercept"], float)

    def test_predict_for_an_untrained_symbol(self, service):
        with pytest.raises(ModelNotFoundError, match="CCC"):
            service.predict("CCC")

    def test_signals_cover_every_trained_symbol(self, service):
        result = service.signals()

        assert [s["symbol"] for s in result["signals"]] == ["AAA", "BBB"]
        assert result["errors"] == []

    def test_signals_report_a_symbol_that_cannot_be_served(self, service, env):
        (env[0] / "BBB.csv").unlink()

        result = service.signals()

        assert [s["symbol"] for s in result["signals"]] == ["AAA"]
        assert result["errors"][0]["symbol"] == "BBB"

    def test_history_returns_the_requested_rows_with_closes(self, service):
        result = service.history("AAA", 25)

        points = result["points"]
        assert len(points) == 25
        assert [p["date"] for p in points] == sorted(p["date"] for p in points)
        assert all(p["close"] > 0 for p in points)

    def test_history_is_capped(self, service):
        result = service.history("AAA", 10_000)

        assert len(result["points"]) == min(MAX_HISTORY_ROWS, 450 - 63)

    def test_history_is_clipped_to_the_data_available(self, service, env):
        write_csv(synthetic_bars(n=120, seed=5), env[0] / "AAA.csv")

        # The 63-day features need 63 prior bars, so 120 bars give 57 complete rows.
        assert len(service.history("AAA", 500)["points"]) == 120 - 63


class TestBacktest:
    def test_returns_both_strategies_and_the_evaluation(self, service):
        result = service.backtest("AAA", **BACKTEST)

        assert result["symbol"] == "AAA"
        assert result["model_version"]
        assert {"direction", "volatility_target", "evaluation"} <= set(result)
        assert result["direction"]["equity_curve"][0]["equity"] == 1_000_000.0
        assert "benchmark" in result["direction"]

    def test_uses_the_requested_capital_and_costs(self, service):
        small = service.backtest("AAA", **{**BACKTEST, "initial_capital": 1000.0})
        free = service.backtest(
            "AAA", **{**BACKTEST, "transaction_cost_bps": 0.0, "slippage_bps": 0.0}
        )
        dear = service.backtest("AAA", **{**BACKTEST, "transaction_cost_bps": 80.0})

        assert small["direction"]["equity_curve"][0]["equity"] == 1000.0
        assert (
            dear["direction"]["metrics"]["total_return"]
            < free["direction"]["metrics"]["total_return"]
        )

    def test_repeat_requests_are_served_from_the_cache(self, service):
        first = service.backtest("AAA", **BACKTEST)

        assert service.backtest("AAA", **BACKTEST) is first

    def test_a_different_parameter_is_a_cache_miss(self, service):
        first = service.backtest("AAA", **BACKTEST)

        assert (
            service.backtest("AAA", **{**BACKTEST, "long_threshold": 0.6}) is not first
        )

    def test_the_cache_is_bounded(self, service, monkeypatch):
        monkeypatch.setattr(service_module, "_BACKTEST_CACHE_SIZE", 2)
        for threshold in (0.51, 0.52, 0.53):
            service.backtest("AAA", **{**BACKTEST, "long_threshold": threshold})

        assert len(service._backtests) == 2

    def test_works_for_a_symbol_without_a_trained_model(self, service):
        result = service.backtest("CCC", **BACKTEST)

        assert result["model_version"] is None
        assert result["evaluation"]["n_oos"] > 0

    def test_too_little_data(self, service, env):
        write_csv(synthetic_bars(n=150, seed=3), env[0] / "CCC.csv")

        with pytest.raises(InsufficientDataError):
            service.backtest("CCC", **BACKTEST)

    def test_unknown_symbol(self, service):
        with pytest.raises(DataNotFoundError):
            service.backtest("ZZZ", **BACKTEST)


class TestTraining:
    def test_trains_the_requested_symbols(self, service):
        trained = service.train(["CCC"])

        assert [t["symbol"] for t in trained] == ["CCC"]
        assert trained[0]["rows"] == 450
        assert "CCC" in service.trained_symbols()
        assert service.predict("CCC")["symbol"] == "CCC"

    def test_defaults_to_every_symbol_with_data(self, service):
        assert [t["symbol"] for t in service.train()] == ["AAA", "BBB", "CCC"]

    def test_symbols_are_normalised(self, service):
        assert service.train(["ccc"])[0]["symbol"] == "CCC"

    def test_unknown_symbols_are_rejected_before_anything_is_trained(self, service):
        before = service.trained_symbols()

        with pytest.raises(DataNotFoundError, match="ZZZ"):
            service.train(["CCC", "ZZZ"])

        assert service.trained_symbols() == before

    def test_can_be_disabled(self, settings):
        locked = AiService(Settings(**{**settings.__dict__, "allow_training": False}))

        with pytest.raises(TrainingDisabledError):
            locked.train(["AAA"])

    def test_only_one_training_run_at_a_time(self, service, monkeypatch):
        started, release = threading.Event(), threading.Event()
        real = service_module.train_and_save

        def slow(*args, **kwargs):
            started.set()
            release.wait(timeout=10)
            return real(*args, **kwargs)

        monkeypatch.setattr(service_module, "train_and_save", slow)
        worker = threading.Thread(target=service.train, args=(["CCC"],))
        worker.start()
        try:
            assert started.wait(timeout=10)
            with pytest.raises(TrainingBusyError):
                service.train(["AAA"])
        finally:
            release.set()
            worker.join(timeout=30)

        assert "CCC" in service.trained_symbols()

    def test_the_lock_is_released_after_a_failure(self, service, env):
        write_csv(synthetic_bars(n=100, seed=3), env[0] / "CCC.csv")

        with pytest.raises(DataError):
            service.train(["CCC"])

        assert service.train(["AAA"])[0]["symbol"] == "AAA"


class TestStartup:
    def test_trains_what_is_missing(self, settings):
        service = AiService(Settings(**{**settings.__dict__, "auto_train": True}))

        outcome = service.startup()

        assert outcome == {"trained": ["CCC"], "failed": []}
        assert service.trained_symbols() == ["AAA", "BBB", "CCC"]

    def test_retrains_stale_models(self, settings, env):
        service = AiService(Settings(**{**settings.__dict__, "auto_train": True}))
        service.startup()
        write_csv(synthetic_bars(n=450, seed=77), env[0] / "AAA.csv")

        assert service.startup() == {"trained": ["AAA"], "failed": []}

    def test_leaves_fresh_models_alone(self, settings):
        service = AiService(Settings(**{**settings.__dict__, "auto_train": True}))
        service.startup()

        assert service.startup() == {"trained": [], "failed": []}

    def test_a_bad_symbol_is_recorded_not_raised(self, settings, env):
        write_csv(synthetic_bars(n=100, seed=3), env[0] / "CCC.csv")
        service = AiService(Settings(**{**settings.__dict__, "auto_train": True}))

        assert service.startup() == {"trained": [], "failed": ["CCC"]}

    def test_does_nothing_when_disabled(self, service):
        assert service.startup() == {"trained": [], "failed": []}
        assert service.trained_symbols() == ["AAA", "BBB"]


def test_a_corrupt_artifact_surfaces_as_an_artifact_error(service, env):
    version = service.registry.latest_version("AAA")
    bundle = env[1] / "AAA" / version / "bundle.joblib"
    bundle.write_bytes(bundle.read_bytes() + b"x")

    with pytest.raises(ArtifactError):
        service.predict("AAA")


def test_walk_forward_defaults_are_unchanged_by_the_service():
    assert WalkForwardConfig().min_train == 252
    assert time.time() > 0
