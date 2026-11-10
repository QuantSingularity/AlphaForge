"""Versioned storage of trained model bundles.

Layout:  <root>/<SYMBOL>/<version>/bundle.joblib
         <root>/<SYMBOL>/<version>/metadata.json
         <root>/<SYMBOL>/latest.txt          (the newest version name)

Bundles are Python pickles (via joblib), so they must only ever be loaded from a
directory you control. A SHA-256 checksum in the metadata detects corruption and
accidental edits; it is not a defence against someone who can write both files.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
import sklearn

from .bundle import ModelBundle
from .data import normalise_symbol
from .exceptions import ArtifactError, ModelNotFoundError

SCHEMA_VERSION = 1
_BUNDLE_FILE = "bundle.joblib"
_METADATA_FILE = "metadata.json"
_LATEST_FILE = "latest.txt"
_VERSION_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$")


@dataclass(frozen=True)
class DataInfo:
    """Which data a bundle was trained on."""

    fingerprint: str
    rows: int
    start: str
    end: str


@dataclass(frozen=True)
class ModelMetadata:
    symbol: str
    version: str
    trained_at: str
    data: DataInfo
    feature_names: tuple[str, ...]
    model_config: dict[str, Any]
    metrics: dict[str, Any] = field(default_factory=dict)
    library_versions: dict[str, str] = field(default_factory=dict)
    checksum: str = ""
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["feature_names"] = list(self.feature_names)
        return out

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ModelMetadata:
        try:
            return cls(
                symbol=payload["symbol"],
                version=payload["version"],
                trained_at=payload["trained_at"],
                data=DataInfo(**payload["data"]),
                feature_names=tuple(payload["feature_names"]),
                model_config=dict(payload["model_config"]),
                metrics=dict(payload.get("metrics", {})),
                library_versions=dict(payload.get("library_versions", {})),
                checksum=payload.get("checksum", ""),
                schema_version=int(payload.get("schema_version", SCHEMA_VERSION)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ArtifactError(f"malformed model metadata: {exc}") from exc


def _library_versions() -> dict[str, str]:
    return {
        "python": ".".join(str(p) for p in sys.version_info[:3]),
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "scikit-learn": sklearn.__version__,
        "joblib": joblib.__version__,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_text_atomic(path: Path, text: str) -> None:
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)


class ModelRegistry:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    # ----------------------------------------------------------------- paths
    def _symbol_dir(self, symbol: str) -> Path:
        return self.root / normalise_symbol(symbol)

    def _version_dir(self, symbol: str, version: str) -> Path:
        if not _VERSION_PATTERN.fullmatch(version):
            raise ModelNotFoundError(f"invalid model version: {version!r}")
        return self._symbol_dir(symbol) / version

    # ----------------------------------------------------------------- write
    def save(
        self,
        bundle: ModelBundle,
        data: DataInfo,
        metrics: dict[str, Any] | None = None,
    ) -> ModelMetadata:
        """Store a bundle as a new version and make it the latest."""
        symbol = normalise_symbol(bundle.symbol)
        symbol_dir = self._symbol_dir(symbol)
        symbol_dir.mkdir(parents=True, exist_ok=True)

        stamp = datetime.now(timezone.utc)
        base = f"{stamp.strftime('%Y%m%dT%H%M%S')}Z-{data.fingerprint[:8]}"
        # Two saves of the same data within one second must not collide.
        version, attempt = base, 1
        while (symbol_dir / version).exists():
            attempt += 1
            version = f"{base}-{attempt}"
        staging = symbol_dir / f".staging-{uuid.uuid4().hex}"
        staging.mkdir()
        try:
            bundle_path = staging / _BUNDLE_FILE
            joblib.dump(bundle, bundle_path, compress=3)
            metadata = ModelMetadata(
                symbol=symbol,
                version=version,
                trained_at=stamp.isoformat(timespec="seconds"),
                data=data,
                feature_names=bundle.direction.feature_names,
                model_config=asdict(bundle.config),
                metrics=metrics or {},
                library_versions=_library_versions(),
                checksum=_sha256(bundle_path),
            )
            (staging / _METADATA_FILE).write_text(
                json.dumps(metadata.to_dict(), indent=2, sort_keys=True),
                encoding="utf-8",
            )
            final = symbol_dir / version
            if final.exists():
                raise ArtifactError(f"model version already exists: {symbol}/{version}")
            os.replace(staging, final)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        _write_text_atomic(symbol_dir / _LATEST_FILE, version)
        return metadata

    # ------------------------------------------------------------------ read
    def list_symbols(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(
            entry.name
            for entry in self.root.iterdir()
            if entry.is_dir() and (entry / _LATEST_FILE).is_file()
        )

    def list_versions(self, symbol: str) -> list[str]:
        symbol_dir = self._symbol_dir(symbol)
        if not symbol_dir.is_dir():
            return []
        return sorted(
            entry.name
            for entry in symbol_dir.iterdir()
            if entry.is_dir() and not entry.name.startswith(".")
        )

    def latest_version(self, symbol: str) -> str:
        pointer = self._symbol_dir(symbol) / _LATEST_FILE
        if not pointer.is_file():
            raise ModelNotFoundError(f"no trained model for {normalise_symbol(symbol)}")
        return pointer.read_text(encoding="utf-8").strip()

    def _resolve(self, symbol: str, version: str | None) -> tuple[str, Path]:
        resolved = (
            self.latest_version(symbol) if version in (None, "latest") else version
        )
        directory = self._version_dir(symbol, resolved)
        if not directory.is_dir():
            raise ModelNotFoundError(f"no model {normalise_symbol(symbol)}/{resolved}")
        return resolved, directory

    def metadata(self, symbol: str, version: str | None = None) -> ModelMetadata:
        _, directory = self._resolve(symbol, version)
        path = directory / _METADATA_FILE
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ArtifactError(f"cannot read metadata {path}: {exc}") from exc
        return ModelMetadata.from_dict(payload)

    def load(self, symbol: str, version: str | None = None) -> ModelBundle:
        """Load a bundle after verifying its checksum."""
        metadata = self.metadata(symbol, version)
        path = self._version_dir(symbol, metadata.version) / _BUNDLE_FILE
        if not path.is_file():
            raise ArtifactError(f"bundle file missing: {path}")
        if metadata.checksum and _sha256(path) != metadata.checksum:
            raise ArtifactError(
                f"checksum mismatch for {metadata.symbol}/{metadata.version}"
            )
        try:
            bundle = joblib.load(path)
        except Exception as exc:  # joblib/pickle raise many types on corrupt input
            raise ArtifactError(f"cannot load bundle {path}: {exc}") from exc
        if not isinstance(bundle, ModelBundle):
            raise ArtifactError(f"{path} does not contain a ModelBundle")
        return bundle
