"""Smoke test for a running AlphaForge AI service (the container or a local run).

Waits for the service to answer, then exercises every read endpoint and one
backtest against symbols that already have data and trained models, and checks
that errors come back in the documented {"error": "..."} shape.

    python scripts/smoke_test_ai_service.py --symbols AAA BBB
    python scripts/smoke_test_ai_service.py --base-url http://127.0.0.1:8090 \\
        --symbols SPY AAPL --timeout 300

Standard library only, so it runs anywhere Python does. Exits 1 on the first
unreachable service and 1 at the end if any check failed.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Sequence


def call(
    base_url: str, method: str, path: str, body: dict[str, Any] | None = None
) -> tuple[int, Any]:
    """Make a request and return (status, parsed JSON). HTTP errors are returned."""
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        base_url + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, raw.decode(errors="replace")


def wait_until_ready(base_url: str, timeout: float) -> bool:
    """Poll /ai/health until it answers 200. Startup may be training models."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            status, _ = call(base_url, "GET", "/ai/health")
            if status == 200:
                return True
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
            pass
        time.sleep(2)
    return False


class Checks:
    """Collects pass/fail results so one failure does not hide the others."""

    def __init__(self) -> None:
        self.failures: list[str] = []

    def check(self, condition: bool, label: str, detail: Any = None) -> None:
        if condition:
            print(f"  ok    {label}")
            return
        print(f"  FAIL  {label}" + (f": {detail}" if detail is not None else ""))
        self.failures.append(label)


def is_probability(value: Any) -> bool:
    return isinstance(value, (int, float)) and 0.0 <= value <= 1.0


def is_finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value)


def run_checks(base_url: str, symbols: list[str]) -> list[str]:
    checks = Checks()
    expected = set(symbols)

    print("GET /ai/health")
    status, health = call(base_url, "GET", "/ai/health")
    checks.check(status == 200, "answers 200", status)
    checks.check(health.get("status") == "ok", "status is ok", health)
    checks.check(
        health.get("data_symbols", 0) >= len(expected),
        "sees the mounted data files",
        health,
    )
    checks.check(
        health.get("models", 0) >= len(expected),
        "has a trained model per symbol",
        health,
    )

    print("GET /ai/models")
    status, listing = call(base_url, "GET", "/ai/models")
    models = (
        {m["symbol"]: m for m in listing.get("models", [])} if status == 200 else {}
    )
    checks.check(expected <= set(models), "lists every expected symbol", sorted(models))
    for symbol in symbols:
        info = models.get(symbol, {})
        checks.check(
            bool(info.get("version")) and info.get("data", {}).get("rows", 0) > 0,
            f"{symbol}: has a version and data span",
            info,
        )
        checks.check(info.get("stale") is False, f"{symbol}: model is not stale")

    print("GET /ai/signals")
    status, signals = call(base_url, "GET", "/ai/signals")
    got = {s["symbol"] for s in signals.get("signals", [])} if status == 200 else set()
    checks.check(expected <= got, "has a signal for every symbol", sorted(got))
    checks.check(signals.get("errors") == [], "reports no per-symbol errors", signals)

    for symbol in symbols:
        print(f"GET /ai/predict/{symbol}")
        status, prediction = call(base_url, "GET", f"/ai/predict/{symbol}")
        checks.check(status == 200, "answers 200", prediction)
        checks.check(is_probability(prediction.get("prob_up")), "prob_up in [0, 1]")
        checks.check(is_finite(prediction.get("signal_weight")), "signal_weight finite")
        checks.check(bool(prediction.get("regime")), "has a regime")
        checks.check(
            isinstance(prediction.get("explanation"), list), "has an explanation"
        )

    first = symbols[0]
    print(f"GET /ai/history/{first}?rows=5")
    status, history = call(base_url, "GET", f"/ai/history/{first}?rows=5")
    checks.check(status == 200, "answers 200", history)
    checks.check(len(history.get("points", [])) == 5, "returns the 5 rows asked for")

    print("POST /ai/backtest")
    status, backtest = call(base_url, "POST", "/ai/backtest", {"symbol": first})
    checks.check(status == 200, "answers 200", backtest)
    checks.check(
        {"evaluation", "direction", "volatility_target"} <= set(backtest),
        "returns evaluation, direction and volatility_target",
        sorted(backtest) if isinstance(backtest, dict) else backtest,
    )

    print("Error handling")
    status, body = call(base_url, "GET", "/ai/predict/NOSUCH")
    checks.check(
        status == 404 and "error" in body, "unknown symbol is 404 with an error body"
    )
    status, body = call(base_url, "POST", "/ai/backtest", {"symbol": first, "bogus": 1})
    checks.check(
        status == 400 and "error" in body, "invalid request is 400 with an error body"
    )
    status, body = call(base_url, "GET", "/ai/no-such-route")
    checks.check(
        status == 404 and "error" in body, "unknown route is 404 with an error body"
    )

    return checks.failures


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base-url", default="http://127.0.0.1:8090")
    parser.add_argument(
        "--symbols",
        nargs="+",
        required=True,
        help="symbols that have data and a trained model (or will after startup)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=180.0,
        help="seconds to wait for the service to become ready (default 180)",
    )
    args = parser.parse_args(argv)
    base_url = args.base_url.rstrip("/")

    print(f"Waiting up to {args.timeout:.0f}s for {base_url}/ai/health")
    if not wait_until_ready(base_url, args.timeout):
        print("The AI service did not become ready in time.", file=sys.stderr)
        return 1

    failures = run_checks(base_url, args.symbols)
    if failures:
        print(f"\n{len(failures)} check(s) failed:", file=sys.stderr)
        for label in failures:
            print(f"  - {label}", file=sys.stderr)
        return 1
    print("\nAll AI service checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
