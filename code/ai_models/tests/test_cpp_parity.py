"""The Python backtester must reproduce the C++ engine exactly.

golden/cpp_backtests.json was captured from the real alphaforge_server (see
golden/regenerate.py). Each scenario replays the weights the engine held, on the
same prices and with the same costs, and requires the same equity curve and
the same metrics, strategy and buy-and-hold benchmark alike.
"""

import json
from pathlib import Path

import numpy as np
import pytest
from ai_models.backtest import run_backtest
from ai_models.config import CostModel

GOLDEN = json.loads(
    (Path(__file__).parent / "golden" / "cpp_backtests.json").read_text()
)
SCENARIOS = GOLDEN["scenarios"]
CLOSES = np.array(GOLDEN["closes"])
DATES = GOLDEN["dates"]

# Both sides do double precision arithmetic in the same order, so they agree to
# rounding noise.
EQUITY_RTOL = 1e-12
METRIC_ATOL = 1e-10


def replay(scenario):
    held = np.array(scenario["held_weights"])
    # The engine reports the weight held during each bar, which is the signal of
    # the bar before. The last signal is never applied, so any value will do.
    signals = np.concatenate((held[1:], [0.0]))
    return run_backtest(
        strategy=scenario["strategy"],
        symbol="GOLD",
        dates=DATES,
        closes=CLOSES,
        weights=signals,
        cost=CostModel(scenario["transaction_cost_bps"], scenario["slippage_bps"]),
        initial_capital=scenario["initial_capital"],
    )


def scenario_id(scenario):
    return f"{scenario['strategy']}-{scenario['label']}"


def test_the_golden_file_covers_every_strategy_and_both_cost_settings():
    pairs = {(s["strategy"], s["label"]) for s in SCENARIOS}

    assert len(pairs) == 8
    assert {s for s, _ in pairs} == {
        "BuyAndHold",
        "MovingAverageCrossover",
        "Momentum",
        "MeanReversion",
    }


def test_the_golden_strategies_actually_trade():
    trades = {
        s["strategy"]: s["metrics"]["num_trades"]
        for s in SCENARIOS
        if s["label"] == "default"
    }

    assert trades["BuyAndHold"] == 1
    assert all(
        trades[name] > 1
        for name in ("MovingAverageCrossover", "Momentum", "MeanReversion")
    )


@pytest.mark.parametrize("scenario", SCENARIOS, ids=scenario_id)
def test_equity_curve_matches_the_engine(scenario):
    result = replay(scenario)

    np.testing.assert_allclose(
        result.equity, scenario["equity"], rtol=EQUITY_RTOL, atol=0.0
    )


@pytest.mark.parametrize("scenario", SCENARIOS, ids=scenario_id)
def test_strategy_metrics_match_the_engine(scenario):
    metrics = replay(scenario).metrics.to_dict()

    for name, expected in scenario["metrics"].items():
        assert metrics[name] == pytest.approx(expected, abs=METRIC_ATOL), name


@pytest.mark.parametrize("scenario", SCENARIOS, ids=scenario_id)
def test_benchmark_metrics_match_the_engine(scenario):
    benchmark = replay(scenario).benchmark.to_dict()

    for name, expected in scenario["benchmark"].items():
        assert benchmark[name] == pytest.approx(expected, abs=METRIC_ATOL), name


@pytest.mark.parametrize("scenario", SCENARIOS, ids=scenario_id)
def test_reported_weights_match_the_engine(scenario):
    curve = replay(scenario).to_dict()["equity_curve"]

    reported = [point["target_weight"] for point in curve]
    assert reported == scenario["held_weights"]
    assert [point["date"] for point in curve] == DATES


def test_a_wrong_cost_would_be_detected():
    """Guards the guard: the comparison really is sensitive to the cost model."""
    scenario = next(
        s for s in SCENARIOS if s["strategy"] == "Momentum" and s["label"] == "default"
    )
    held = np.array(scenario["held_weights"])
    wrong = run_backtest(
        "x",
        "GOLD",
        DATES,
        CLOSES,
        np.concatenate((held[1:], [0.0])),
        CostModel(0.0, 0.0),
        scenario["initial_capital"],
    )

    assert not np.allclose(wrong.equity, scenario["equity"], rtol=EQUITY_RTOL)
