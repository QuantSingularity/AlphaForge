"""Service configuration, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from ai_models.config import (
    ARTIFACTS_DIR_ENV,
    DATA_DIR_ENV,
    default_artifacts_dir,
    default_data_dir,
)

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _flag(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    lowered = value.strip().lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise ValueError(f"cannot interpret {value!r} as a boolean")


@dataclass(frozen=True)
class Settings:
    """Where data and models live, and what the service is allowed to do.

    Environment variables:
      ALPHAFORGE_DATA_DIR    directory of <SYMBOL>.csv files
      ALPHAFORGE_MODEL_DIR   where trained models are stored
      AI_AUTO_TRAIN          train missing or out-of-date models at startup (default on)
      AI_ALLOW_TRAINING      allow POST /ai/train (default on)
      AI_HOST, AI_PORT       listen address (default 127.0.0.1:8090)
    """

    data_dir: Path
    artifacts_dir: Path
    auto_train: bool = True
    allow_training: bool = True
    host: str = "127.0.0.1"
    port: int = 8090

    def __post_init__(self) -> None:
        if not 0 < self.port < 65536:
            raise ValueError("port must be between 1 and 65535")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        data_dir = (
            Path(env[DATA_DIR_ENV]) if env.get(DATA_DIR_ENV) else default_data_dir()
        )
        artifacts = (
            Path(env[ARTIFACTS_DIR_ENV])
            if env.get(ARTIFACTS_DIR_ENV)
            else default_artifacts_dir()
        )
        try:
            port = int(env.get("AI_PORT", "8090"))
        except ValueError as exc:
            raise ValueError("AI_PORT must be an integer") from exc
        return cls(
            data_dir=data_dir,
            artifacts_dir=artifacts,
            auto_train=_flag(env.get("AI_AUTO_TRAIN"), True),
            allow_training=_flag(env.get("AI_ALLOW_TRAINING"), True),
            host=env.get("AI_HOST", "127.0.0.1") or "127.0.0.1",
            port=port,
        )
