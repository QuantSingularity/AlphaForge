"""Regenerate cpp_backtests.json from the real C++ engine.

The golden file pins the Python backtester to the engine: each scenario stores
the weights the engine held, the equity curve it produced and its metrics, for a
fixed synthetic price series. test_cpp_parity.py replays the same weights through
ai_models and requires identical results.

Run this only when the engine's cost model or metric definitions change on
purpose (and update ai_models.backtest / ai_models.metrics to match):

    python code/ai_models/tests/golden/regenerate.py path/to/alphaforge_server
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(CODE_DIR))

from ai_models.data import read_bars  # noqa: E402
from ai_models.testing import synthetic_bars, write_csv  # noqa: E402

STRATEGIES = ["BuyAndHold", "MovingAverageCrossover", "Momentum", "MeanReversion"]
COST_SETTINGS = {
    "default": {},
    "custom_costs": {
        "transaction_cost_bps": 12.5,
        "slippage_bps": 3.0,
        "initial_capital": 250000.0,
    },
}
OUTPUT = Path(__file__).with_name("cpp_backtests.json")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _post(base: str, body: dict) -> dict:
    request = urllib.request.Request(
        f"{base}/api/backtest",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(request, timeout=30).read())


def main(server_binary: str) -> None:
    bars_in = synthetic_bars(
        n=160, seed=42, daily_drift=0.0004, base_volatility=0.012, start_price=50.0
    )
    with tempfile.TemporaryDirectory() as tmp:
        write_csv(bars_in, Path(tmp) / "GOLD.csv")
        bars = read_bars(Path(tmp) / "GOLD.csv")  # exactly what the engine reads
        port = _free_port()
        server = subprocess.Popen(
            [server_binary, str(port), tmp, str(Path(tmp) / "no_web")],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        base = f"http://127.0.0.1:{port}"
        try:
            for _ in range(50):
                try:
                    urllib.request.urlopen(f"{base}/api/health", timeout=1)
                    break
                except OSError:
                    time.sleep(0.2)
            scenarios = []
            for strategy in STRATEGIES:
                for label, extra in COST_SETTINGS.items():
                    result = _post(
                        base, {"symbol": "GOLD", "strategy": strategy, **extra}
                    )
                    curve = result["equity_curve"]
                    scenarios.append(
                        {
                            "strategy": strategy,
                            "label": label,
                            "transaction_cost_bps": extra.get(
                                "transaction_cost_bps", 5.0
                            ),
                            "slippage_bps": extra.get("slippage_bps", 2.0),
                            "initial_capital": extra.get(
                                "initial_capital", 1_000_000.0
                            ),
                            "held_weights": [p["target_weight"] for p in curve],
                            "equity": [p["equity"] for p in curve],
                            "metrics": result["metrics"],
                            "benchmark": result["benchmark"],
                        }
                    )
        finally:
            server.terminate()
            server.wait(timeout=10)

    payload = {
        "generated_by": "alphaforge_server /api/backtest (see regenerate.py)",
        "dates": [d.strftime("%Y-%m-%d") for d in bars.index],
        "closes": bars["close"].tolist(),
        "scenarios": scenarios,
    }
    OUTPUT.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {OUTPUT} ({OUTPUT.stat().st_size} bytes, {len(scenarios)} scenarios)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(sys.argv[1])
