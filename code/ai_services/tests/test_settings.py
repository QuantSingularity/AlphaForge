from pathlib import Path

import pytest
from ai_models.config import (
    ARTIFACTS_DIR_ENV,
    DATA_DIR_ENV,
    default_artifacts_dir,
    default_data_dir,
)
from ai_services.settings import Settings


class TestFromEnv:
    def test_defaults(self):
        settings = Settings.from_env({})

        assert settings.data_dir == default_data_dir()
        assert settings.artifacts_dir == default_artifacts_dir()
        assert settings.auto_train is True
        assert settings.allow_training is True
        assert (settings.host, settings.port) == ("127.0.0.1", 8090)

    def test_reads_every_variable(self, tmp_path):
        settings = Settings.from_env(
            {
                DATA_DIR_ENV: str(tmp_path / "d"),
                ARTIFACTS_DIR_ENV: str(tmp_path / "a"),
                "AI_AUTO_TRAIN": "0",
                "AI_ALLOW_TRAINING": "false",
                "AI_HOST": "0.0.0.0",
                "AI_PORT": "9000",
            }
        )

        assert settings.data_dir == tmp_path / "d"
        assert settings.artifacts_dir == tmp_path / "a"
        assert settings.auto_train is False
        assert settings.allow_training is False
        assert (settings.host, settings.port) == ("0.0.0.0", 9000)

    @pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on", " On "])
    def test_truthy_flags(self, raw):
        assert Settings.from_env({"AI_AUTO_TRAIN": raw}).auto_train is True

    @pytest.mark.parametrize("raw", ["0", "false", "No", "off"])
    def test_falsy_flags(self, raw):
        assert Settings.from_env({"AI_AUTO_TRAIN": raw}).auto_train is False

    @pytest.mark.parametrize("raw", ["", "  "])
    def test_blank_flags_use_the_default(self, raw):
        assert Settings.from_env({"AI_ALLOW_TRAINING": raw}).allow_training is True

    def test_an_unrecognised_flag_is_an_error(self):
        with pytest.raises(ValueError, match="boolean"):
            Settings.from_env({"AI_AUTO_TRAIN": "maybe"})

    def test_a_non_numeric_port_is_an_error(self):
        with pytest.raises(ValueError, match="AI_PORT"):
            Settings.from_env({"AI_PORT": "eighty"})

    @pytest.mark.parametrize("port", ["0", "70000", "-1"])
    def test_an_out_of_range_port_is_an_error(self, port):
        with pytest.raises(ValueError, match="port"):
            Settings.from_env({"AI_PORT": port})

    def test_an_empty_host_falls_back_to_localhost(self):
        assert Settings.from_env({"AI_HOST": ""}).host == "127.0.0.1"

    def test_is_immutable(self):
        with pytest.raises(AttributeError):
            Settings.from_env({}).port = 1


def test_paths_are_path_objects():
    settings = Settings.from_env({DATA_DIR_ENV: "relative/data"})

    assert isinstance(settings.data_dir, Path)
