"""Run the service:  python -m ai_services [--host H] [--port P]"""

from __future__ import annotations

import argparse
import dataclasses
import logging
from pathlib import Path
from typing import Sequence

import uvicorn

from .app import create_app
from .settings import Settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai_services", description="AlphaForge AI service"
    )
    parser.add_argument(
        "--host", default=None, help="listen address (default AI_HOST or 127.0.0.1)"
    )
    parser.add_argument(
        "--port", type=int, default=None, help="listen port (default AI_PORT or 8090)"
    )
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--artifacts-dir", type=Path, default=None)
    parser.add_argument(
        "--no-auto-train", action="store_true", help="do not train at startup"
    )
    parser.add_argument(
        "--log-level", default="info", choices=["debug", "info", "warning", "error"]
    )
    return parser


def settings_from_args(args: argparse.Namespace) -> Settings:
    settings = Settings.from_env()
    changes: dict[str, object] = {}
    if args.host is not None:
        changes["host"] = args.host
    if args.port is not None:
        changes["port"] = args.port
    if args.data_dir is not None:
        changes["data_dir"] = args.data_dir
    if args.artifacts_dir is not None:
        changes["artifacts_dir"] = args.artifacts_dir
    if args.no_auto_train:
        changes["auto_train"] = False
    return dataclasses.replace(settings, **changes) if changes else settings


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = settings_from_args(args)
    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level=args.log_level,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
