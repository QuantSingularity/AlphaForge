import json
import warnings

import joblib
import numpy as np
import pytest
from ai_models import registry as registry_module
from ai_models.exceptions import ArtifactError, DataError, ModelNotFoundError
from ai_models.features import latest_features
from ai_models.registry import DataInfo, ModelMetadata

INFO = DataInfo(
    fingerprint="abcdef0123456789", rows=600, start="2020-01-01", end="2022-05-02"
)


def save(registry, bundle, **kwargs):
    return registry.save(bundle, INFO, kwargs.get("metrics", {"accuracy": 0.5}))


class TestSaveAndLoad:
    def test_a_loaded_bundle_predicts_like_the_original(
        self, registry, fitted_bundle, bars
    ):
        save(registry, fitted_bundle)

        loaded = registry.load("TEST")
        features = latest_features(bars, 20)

        np.testing.assert_array_equal(
            loaded.direction.predict_proba_up(features),
            fitted_bundle.direction.predict_proba_up(features),
        )
        np.testing.assert_array_equal(
            loaded.volatility.forecast_series(bars).to_numpy(),
            fitted_bundle.volatility.forecast_series(bars).to_numpy(),
        )
        assert loaded.regime.predict(features) == fitted_bundle.regime.predict(features)
        np.testing.assert_array_equal(
            loaded.anomaly.score(features), fitted_bundle.anomaly.score(features)
        )

    def test_the_metadata_describes_the_bundle(self, registry, fitted_bundle):
        metadata = save(registry, fitted_bundle)

        assert metadata.symbol == "TEST"
        assert metadata.data == INFO
        assert metadata.feature_names == fitted_bundle.direction.feature_names
        assert metadata.model_config["direction_model"] == "logistic"
        assert metadata.metrics == {"accuracy": 0.5}
        assert len(metadata.checksum) == 64
        assert metadata.version.endswith("-abcdef01")
        assert metadata.library_versions["scikit-learn"]
        assert metadata.schema_version == 1

    def test_metadata_round_trips_through_the_registry(self, registry, fitted_bundle):
        saved = save(registry, fitted_bundle)

        assert registry.metadata("test") == saved

    def test_metadata_on_disk_is_plain_json(self, registry, fitted_bundle, tmp_path):
        metadata = save(registry, fitted_bundle)

        path = registry.root / "TEST" / metadata.version / "metadata.json"
        payload = json.loads(path.read_text())
        assert payload["symbol"] == "TEST"
        assert payload["data"]["rows"] == 600

    def test_symbols_are_case_insensitive(self, registry, fitted_bundle):
        save(registry, fitted_bundle)

        assert registry.load("test").symbol == "TEST"


class TestVersions:
    def test_the_newest_version_is_the_latest(self, registry, fitted_bundle):
        first = save(registry, fitted_bundle)
        second = save(registry, fitted_bundle)

        assert registry.latest_version("TEST") == second.version
        assert registry.list_versions("TEST") == sorted([first.version, second.version])

    def test_two_saves_in_the_same_second_get_different_versions(
        self, registry, fitted_bundle
    ):
        versions = {save(registry, fitted_bundle).version for _ in range(4)}

        assert len(versions) == 4

    def test_an_older_version_can_be_loaded_explicitly(self, registry, fitted_bundle):
        first = save(registry, fitted_bundle)
        save(registry, fitted_bundle)

        assert registry.metadata("TEST", first.version).version == first.version
        assert registry.load("TEST", first.version).symbol == "TEST"

    def test_latest_can_be_requested_by_name(self, registry, fitted_bundle):
        saved = save(registry, fitted_bundle)

        assert registry.metadata("TEST", "latest").version == saved.version

    def test_symbols_are_listed_once_trained(self, registry, fitted_bundle):
        assert registry.list_symbols() == []
        save(registry, fitted_bundle)

        assert registry.list_symbols() == ["TEST"]

    def test_unknown_symbols_have_no_versions(self, registry):
        assert registry.list_versions("NOPE") == []


class TestMissingModels:
    def test_no_model_for_a_symbol(self, registry):
        with pytest.raises(ModelNotFoundError, match="no trained model for NOPE"):
            registry.load("nope")

    def test_no_such_version(self, registry, fitted_bundle):
        save(registry, fitted_bundle)

        with pytest.raises(ModelNotFoundError, match="no model TEST/19990101"):
            registry.load("TEST", "19990101")

    @pytest.mark.parametrize("version", ["../x", "a/b", "", ".hidden", "a" * 80])
    def test_unsafe_version_names_are_rejected(self, registry, fitted_bundle, version):
        save(registry, fitted_bundle)

        with pytest.raises(ModelNotFoundError, match="invalid model version"):
            registry.load("TEST", version)

    @pytest.mark.parametrize("symbol", ["../x", "a/b", ".hidden", ""])
    def test_unsafe_symbols_are_rejected(self, registry, symbol):
        with pytest.raises(DataError, match="invalid symbol"):
            registry.load(symbol)


class TestIntegrity:
    def bundle_path(self, registry, metadata):
        return registry.root / "TEST" / metadata.version / "bundle.joblib"

    def test_a_modified_bundle_is_rejected(self, registry, fitted_bundle):
        metadata = save(registry, fitted_bundle)
        path = self.bundle_path(registry, metadata)
        path.write_bytes(path.read_bytes() + b"tampered")

        with pytest.raises(ArtifactError, match="checksum mismatch"):
            registry.load("TEST")

    def test_a_missing_bundle_file(self, registry, fitted_bundle):
        metadata = save(registry, fitted_bundle)
        self.bundle_path(registry, metadata).unlink()

        with pytest.raises(ArtifactError, match="bundle file missing"):
            registry.load("TEST")

    def test_unreadable_metadata(self, registry, fitted_bundle):
        metadata = save(registry, fitted_bundle)
        (registry.root / "TEST" / metadata.version / "metadata.json").write_text(
            "{not json"
        )

        with pytest.raises(ArtifactError, match="cannot read metadata"):
            registry.load("TEST")

    def test_incomplete_metadata(self, registry, fitted_bundle):
        metadata = save(registry, fitted_bundle)
        (registry.root / "TEST" / metadata.version / "metadata.json").write_text("{}")

        with pytest.raises(ArtifactError, match="malformed model metadata"):
            registry.load("TEST")

    def test_a_file_that_is_not_a_bundle(self, registry, fitted_bundle):
        metadata = save(registry, fitted_bundle)
        path = self.bundle_path(registry, metadata)
        joblib.dump({"not": "a bundle"}, path)
        meta_path = path.with_name("metadata.json")
        payload = json.loads(meta_path.read_text())
        payload["checksum"] = registry_module._sha256(path)
        meta_path.write_text(json.dumps(payload))

        with pytest.raises(ArtifactError, match="does not contain a ModelBundle"):
            registry.load("TEST")

    def test_an_undecodable_bundle(self, registry, fitted_bundle):
        metadata = save(registry, fitted_bundle)
        path = self.bundle_path(registry, metadata)
        path.write_bytes(b"garbage")
        meta_path = path.with_name("metadata.json")
        payload = json.loads(meta_path.read_text())
        payload["checksum"] = registry_module._sha256(path)
        meta_path.write_text(json.dumps(payload))

        with pytest.raises(ArtifactError, match="cannot load bundle"):
            registry.load("TEST")


class TestAtomicity:
    def test_a_failed_save_leaves_nothing_behind(
        self, registry, fitted_bundle, monkeypatch
    ):
        good = save(registry, fitted_bundle)

        def boom(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(registry_module.joblib, "dump", boom)
        with pytest.raises(OSError, match="disk full"):
            save(registry, fitted_bundle)

        assert registry.list_versions("TEST") == [good.version]
        assert registry.latest_version("TEST") == good.version
        assert not [
            p
            for p in (registry.root / "TEST").iterdir()
            if p.name.startswith(".staging")
        ]

    def test_the_latest_pointer_is_written_only_after_the_version_exists(
        self, registry, fitted_bundle
    ):
        metadata = save(registry, fitted_bundle)

        assert (registry.root / "TEST" / metadata.version / "metadata.json").is_file()
        assert (registry.root / "TEST" / "latest.txt").read_text() == metadata.version


def test_metadata_from_dict_requires_the_core_fields():
    with pytest.raises(ArtifactError, match="malformed"):
        ModelMetadata.from_dict({"symbol": "X"})


def test_no_warnings_are_raised_when_saving(registry, fitted_bundle):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        save(registry, fitted_bundle)
