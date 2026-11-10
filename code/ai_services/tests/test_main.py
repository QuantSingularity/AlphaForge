import logging

import pytest
from ai_services import __main__ as entry


def parse(*argv):
    return entry.build_parser().parse_args(list(argv))


class TestSettingsFromArgs:
    def test_without_flags_the_environment_decides(self, monkeypatch):
        monkeypatch.setenv("AI_PORT", "9123")

        assert entry.settings_from_args(parse()).port == 9123

    def test_flags_override_the_environment(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AI_PORT", "9123")

        settings = entry.settings_from_args(
            parse(
                "--host",
                "0.0.0.0",
                "--port",
                "9000",
                "--data-dir",
                str(tmp_path / "d"),
                "--artifacts-dir",
                str(tmp_path / "a"),
                "--no-auto-train",
            )
        )

        assert (settings.host, settings.port) == ("0.0.0.0", 9000)
        assert settings.data_dir == tmp_path / "d"
        assert settings.artifacts_dir == tmp_path / "a"
        assert settings.auto_train is False

    def test_auto_train_stays_on_by_default(self):
        assert entry.settings_from_args(parse()).auto_train is True


class TestParser:
    def test_rejects_an_unknown_log_level(self):
        with pytest.raises(SystemExit):
            parse("--log-level", "chatty")

    def test_rejects_a_non_numeric_port(self):
        with pytest.raises(SystemExit):
            parse("--port", "abc")


def test_main_starts_uvicorn_with_the_resolved_settings(monkeypatch, tmp_path):
    captured = {}

    def fake_run(app, host, port, log_level):
        captured.update(app=app, host=host, port=port, log_level=log_level)

    monkeypatch.setattr(entry.uvicorn, "run", fake_run)

    code = entry.main(
        [
            "--host",
            "127.0.0.9",
            "--port",
            "8765",
            "--data-dir",
            str(tmp_path),
            "--artifacts-dir",
            str(tmp_path),
            "--no-auto-train",
            "--log-level",
            "warning",
        ]
    )

    assert code == 0
    assert (captured["host"], captured["port"], captured["log_level"]) == (
        "127.0.0.9",
        8765,
        "warning",
    )
    assert captured["app"].title == "AlphaForge AI Service"
    logging.getLogger().handlers.clear()
