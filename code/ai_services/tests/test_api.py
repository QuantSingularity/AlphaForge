import threading

import pytest
from ai_models.testing import synthetic_bars, write_csv
from ai_services import service as service_module
from ai_services.app import create_app
from ai_services.service import AiService
from ai_services.settings import Settings
from fastapi.testclient import TestClient

BACKTEST = {"symbol": "AAA"}


def assert_error(response, status, fragment=None):
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"error"}
    if fragment is not None:
        assert fragment.lower() in body["error"].lower()
    return body["error"]


class TestHealthAndModels:
    def test_health(self, client):
        body = client.get("/ai/health").json()

        assert body["status"] == "ok"
        assert body["models"] == 2
        assert body["data_symbols"] == 3

    def test_models(self, client):
        models = client.get("/ai/models").json()["models"]

        assert [m["symbol"] for m in models] == ["AAA", "BBB"]
        assert models[0]["data"]["rows"] == 450
        assert models[0]["stale"] is False

    def test_interactive_docs_and_schema_are_served(self, client):
        schema = client.get("/ai/openapi.json").json()

        assert client.get("/ai/docs").status_code == 200
        assert {
            "/ai/health",
            "/ai/predict/{symbol}",
            "/ai/backtest",
            "/ai/train",
        } <= set(schema["paths"])


class TestPredict:
    def test_returns_the_documented_fields(self, client):
        body = client.get("/ai/predict/AAA").json()

        assert body["symbol"] == "AAA"
        assert set(body) >= {
            "model_version",
            "date",
            "prob_up",
            "signal_weight",
            "forecast_vol",
            "regime",
            "regime_probabilities",
            "anomaly_score",
            "is_anomaly",
            "intercept",
            "explanation",
        }
        assert body["explanation"][0].keys() == {"feature", "contribution"}

    def test_symbols_are_case_insensitive(self, client):
        assert client.get("/ai/predict/aaa").json()["symbol"] == "AAA"

    def test_untrained_symbol(self, client):
        assert_error(client.get("/ai/predict/CCC"), 404, "no trained model")

    def test_unknown_symbol_does_not_reveal_server_paths(self, client, env):
        error = assert_error(client.get("/ai/predict/ZZZ"), 404)

        assert str(env[0]) not in error and str(env[1]) not in error

    @pytest.mark.parametrize("symbol", ["bad%20name", "a" * 17, ".hidden", "-x"])
    def test_invalid_symbols_are_rejected_before_any_lookup(self, client, symbol):
        assert_error(client.get(f"/ai/predict/{symbol}"), 400, "invalid request")

    def test_path_traversal_never_matches_a_route(self, client):
        assert_error(client.get("/ai/predict/..%2F..%2Fetc%2Fpasswd"), 404)


class TestSignalsAndHistory:
    def test_signals(self, client):
        body = client.get("/ai/signals").json()

        assert [s["symbol"] for s in body["signals"]] == ["AAA", "BBB"]
        assert body["errors"] == []

    def test_history_defaults_to_120_rows(self, client):
        points = client.get("/ai/history/AAA").json()["points"]

        assert len(points) == 120
        assert points[0].keys() >= {
            "date",
            "close",
            "prob_up",
            "regime",
            "anomaly_score",
        }

    def test_history_rows_parameter(self, client):
        assert len(client.get("/ai/history/AAA?rows=7").json()["points"]) == 7

    @pytest.mark.parametrize("rows", ["0", "501", "abc", "-5"])
    def test_history_rows_are_validated(self, client, rows):
        assert_error(client.get(f"/ai/history/AAA?rows={rows}"), 400, "rows")

    def test_history_of_an_untrained_symbol(self, client):
        assert_error(client.get("/ai/history/CCC"), 404)


class TestBacktest:
    def test_returns_both_strategies(self, client):
        body = client.post("/ai/backtest", json=BACKTEST).json()

        assert body["symbol"] == "AAA"
        assert (
            body["direction"]["equity_curve"]
            and body["volatility_target"]["equity_curve"]
        )
        assert body["evaluation"]["classification"]["n"] > 0
        point = body["direction"]["equity_curve"][0]
        assert point.keys() == {"date", "equity", "target_weight"}

    def test_has_the_engine_response_shape(self, client):
        body = client.post("/ai/backtest", json=BACKTEST).json()

        for strategy in (body["direction"], body["volatility_target"]):
            assert strategy.keys() == {
                "strategy",
                "symbol",
                "equity_curve",
                "metrics",
                "benchmark",
            }
            assert strategy["metrics"].keys() == strategy["benchmark"].keys()

    def test_honours_the_cost_and_capital_fields(self, client):
        body = client.post(
            "/ai/backtest",
            json={
                "symbol": "AAA",
                "initial_capital": 5000,
                "transaction_cost_bps": 0,
                "slippage_bps": 0,
            },
        ).json()

        assert body["direction"]["equity_curve"][0]["equity"] == 5000

    @pytest.mark.parametrize(
        ("payload", "fragment"),
        [
            ({}, "symbol"),
            ({"symbol": "../x"}, "symbol"),
            ({"symbol": "AAA", "transaction_cost_bps": -1}, "transaction_cost_bps"),
            ({"symbol": "AAA", "slippage_bps": 5000}, "slippage_bps"),
            ({"symbol": "AAA", "initial_capital": 0}, "initial_capital"),
            ({"symbol": "AAA", "target_volatility": 0}, "target_volatility"),
            ({"symbol": "AAA", "long_threshold": 1.0}, "long_threshold"),
            ({"symbol": "AAA", "strategy": "Momentum"}, "strategy"),
            ({"symbol": "AAA", "initial_capital": "lots"}, "initial_capital"),
        ],
    )
    def test_invalid_requests_are_rejected(self, client, payload, fragment):
        assert_error(client.post("/ai/backtest", json=payload), 400, fragment)

    def test_a_non_json_body_is_a_client_error(self, client):
        response = client.post(
            "/ai/backtest",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )

        assert_error(response, 400)

    def test_unknown_symbol(self, client):
        assert_error(client.post("/ai/backtest", json={"symbol": "ZZZ"}), 404)

    def test_too_little_data(self, client, env):
        write_csv(synthetic_bars(n=150, seed=3), env[0] / "CCC.csv")

        assert_error(
            client.post("/ai/backtest", json={"symbol": "CCC"}), 422, "too few"
        )


class TestTrain:
    def test_trains_specific_symbols(self, client):
        body = client.post("/ai/train", json={"symbols": ["CCC"]}).json()

        assert [t["symbol"] for t in body["trained"]] == ["CCC"]
        assert client.get("/ai/predict/CCC").status_code == 200

    def test_trains_everything_without_a_body(self, client):
        body = client.post("/ai/train").json()

        assert [t["symbol"] for t in body["trained"]] == ["AAA", "BBB", "CCC"]

    def test_unknown_symbol(self, client):
        assert_error(client.post("/ai/train", json={"symbols": ["ZZZ"]}), 404, "ZZZ")

    def test_invalid_symbol(self, client):
        assert_error(client.post("/ai/train", json={"symbols": ["a/b"]}), 400)

    def test_extra_fields_are_rejected(self, client):
        assert_error(
            client.post("/ai/train", json={"symbols": [], "force": True}), 400, "force"
        )

    def test_too_many_symbols(self, client):
        assert_error(client.post("/ai/train", json={"symbols": ["A"] * 51}), 400)

    def test_can_be_disabled(self, settings):
        locked = Settings(**{**settings.__dict__, "allow_training": False})
        with TestClient(create_app(locked), raise_server_exceptions=False) as client:
            assert_error(
                client.post("/ai/train", json={"symbols": ["AAA"]}), 403, "disabled"
            )

    def test_a_concurrent_run_is_a_conflict(self, settings, monkeypatch):
        service = AiService(settings)
        started, release = threading.Event(), threading.Event()
        real = service_module.train_and_save

        def slow(*args, **kwargs):
            started.set()
            release.wait(timeout=10)
            return real(*args, **kwargs)

        monkeypatch.setattr(service_module, "train_and_save", slow)
        with TestClient(
            create_app(settings, service), raise_server_exceptions=False
        ) as client:
            first: dict = {}
            worker = threading.Thread(
                target=lambda: first.update(
                    response=client.post("/ai/train", json={"symbols": ["CCC"]})
                )
            )
            worker.start()
            try:
                assert started.wait(timeout=10)
                assert_error(
                    client.post("/ai/train", json={"symbols": ["AAA"]}),
                    409,
                    "in progress",
                )
            finally:
                release.set()
                worker.join(timeout=30)

        assert first["response"].status_code == 200


class TestErrorHandling:
    def test_unknown_routes_use_the_error_shape(self, client):
        assert_error(client.get("/ai/nope"), 404)

    def test_wrong_methods_use_the_error_shape(self, client):
        assert_error(client.post("/ai/health"), 405)

    def test_malformed_data_is_unprocessable(self, client, env):
        (env[0] / "CCC.csv").write_text(
            "date,open,high,low,close,volume\n2024-01-02,10,9,11,10,1\n"
        )

        assert_error(
            client.post("/ai/train", json={"symbols": ["CCC"]}), 422, "high below low"
        )

    def test_a_tampered_model_is_a_generic_500_without_paths(
        self, client, service, env
    ):
        version = service.registry.latest_version("AAA")
        bundle = env[1] / "AAA" / version / "bundle.joblib"
        bundle.write_bytes(bundle.read_bytes() + b"x")

        error = assert_error(client.get("/ai/predict/AAA"), 500)

        assert error == "model artifact could not be loaded"
        assert str(env[1]) not in error

    def test_an_unexpected_failure_is_a_generic_500(self, client, service, monkeypatch):
        def boom(_):
            raise RuntimeError("secret internal detail /etc/shadow")

        monkeypatch.setattr(service, "predict", boom)

        error = assert_error(client.get("/ai/predict/AAA"), 500)

        assert error == "internal server error"
        assert "shadow" not in error

    def test_errors_are_json(self, client):
        assert (
            client.get("/ai/predict/ZZZ")
            .headers["content-type"]
            .startswith("application/json")
        )


class TestStartup:
    def test_auto_train_trains_missing_models_on_startup(self, settings):
        enabled = Settings(**{**settings.__dict__, "auto_train": True})

        with TestClient(create_app(enabled), raise_server_exceptions=False) as client:
            assert client.get("/ai/health").json()["models"] == 3

    def test_without_auto_train_nothing_is_trained(self, client):
        assert client.get("/ai/health").json()["models"] == 2

    def test_the_service_is_reachable_from_the_app_state(self, settings, service):
        assert create_app(settings, service).state.service is service

    def test_the_app_builds_its_own_service_from_the_environment(
        self, monkeypatch, env
    ):
        monkeypatch.setenv("ALPHAFORGE_DATA_DIR", str(env[0]))
        monkeypatch.setenv("ALPHAFORGE_MODEL_DIR", str(env[1]))
        monkeypatch.setenv("AI_AUTO_TRAIN", "0")

        with TestClient(create_app()) as client:
            assert client.get("/ai/health").json()["models"] == 2
