import numpy as np
import pytest
from ai_models.backtest import BacktestResult, run_backtest, simulate
from ai_models.config import CostModel

CLOSES = np.array([100.0, 101.0, 99.0, 102.0, 104.0])
DATES = ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
NO_COST = CostModel(0.0, 0.0)


class TestSimulate:
    def test_buy_and_hold_without_costs_tracks_the_price(self):
        equity, trades = simulate(np.ones(5), CLOSES, NO_COST, 1000.0)

        np.testing.assert_allclose(equity, 1000.0 * CLOSES / CLOSES[0])
        assert trades == 1

    def test_the_first_bar_pays_the_entry_cost(self):
        equity, _ = simulate(np.ones(5), CLOSES, CostModel(5.0, 2.0), 1_000_000.0)

        assert equity[1] == pytest.approx(1_000_000.0 * (1 + 0.01 - 0.0007))

    def test_a_signal_earns_the_return_of_the_next_bar_only(self):
        # Long after bar 0, flat after bar 1: only the 0 -> 1 move is earned.
        weights = np.array([1.0, 0.0, 0.0, 0.0, 0.0])

        equity, trades = simulate(weights, CLOSES, NO_COST, 100.0)

        np.testing.assert_allclose(equity, [100.0, 101.0, 101.0, 101.0, 101.0])
        assert trades == 2

    def test_the_last_weight_is_never_applied(self):
        low, _ = simulate(np.array([1.0, 1.0, 1.0, 1.0, 0.0]), CLOSES, NO_COST)
        high, _ = simulate(np.array([1.0, 1.0, 1.0, 1.0, 1.0]), CLOSES, NO_COST)

        np.testing.assert_allclose(low, high)

    def test_flat_weights_keep_equity_constant(self):
        equity, trades = simulate(np.zeros(5), CLOSES, CostModel(), 500.0)

        np.testing.assert_allclose(equity, 500.0)
        assert trades == 0

    def test_costs_scale_with_turnover(self):
        weights = np.array([0.0, 0.5, 0.5, 1.0, 1.0])

        cheap, _ = simulate(weights, CLOSES, CostModel(1.0, 0.0))
        dear, _ = simulate(weights, CLOSES, CostModel(50.0, 0.0))

        assert dear[-1] < cheap[-1]

    def test_partial_weights_earn_a_fraction_of_the_return(self):
        equity, _ = simulate(np.full(5, 0.5), CLOSES, NO_COST, 100.0)

        assert equity[1] == pytest.approx(100.0 * (1 + 0.5 * 0.01))

    def test_a_single_bar_has_no_returns(self):
        equity, trades = simulate(np.array([1.0]), np.array([10.0]), NO_COST, 50.0)

        np.testing.assert_allclose(equity, [50.0])
        assert trades == 0

    @pytest.mark.parametrize(
        ("weights", "message"),
        [
            (np.array([1.0, 1.0]), "equal in length"),
            (np.array([[1.0] * 5]), "one-dimensional"),
            (np.array([1.0, 1.0, np.nan, 1.0, 1.0]), "finite"),
            (np.array([1.0, 1.0, 1.5, 1.0, 1.0]), r"\[0, 1\]"),
            (np.array([1.0, -0.1, 1.0, 1.0, 1.0]), r"\[0, 1\]"),
        ],
    )
    def test_invalid_weights_are_rejected(self, weights, message):
        with pytest.raises(ValueError, match=message):
            simulate(weights, CLOSES)

    def test_non_positive_prices_are_rejected(self):
        with pytest.raises(ValueError, match="positive"):
            simulate(np.ones(3), np.array([1.0, 0.0, 2.0]))


class TestRunBacktest:
    def test_result_carries_the_curve_and_both_metric_sets(self):
        result = run_backtest("S", "XYZ", DATES, CLOSES, np.array([1, 1, 0, 1, 1.0]))

        assert isinstance(result, BacktestResult)
        assert result.equity.size == 5
        assert result.returns.size == 4
        assert result.metrics.num_trades == 3
        assert result.benchmark.num_trades == 1

    def test_benchmark_is_buy_and_hold_over_the_same_window(self):
        result = run_backtest("S", "XYZ", DATES, CLOSES, np.zeros(5), NO_COST)

        assert result.benchmark.total_return == pytest.approx(0.04)
        assert result.metrics.total_return == 0.0

    def test_held_weights_are_the_signals_shifted_by_one_bar(self):
        signals = np.array([1.0, 0.0, 1.0, 0.5, 0.0])

        result = run_backtest("S", "XYZ", DATES, CLOSES, signals)

        np.testing.assert_array_equal(result.held_weights, [1.0, 1.0, 0.0, 1.0, 0.5])

    def test_to_dict_matches_the_engine_response_shape(self):
        payload = run_backtest("S", "XYZ", DATES, CLOSES, np.ones(5)).to_dict()

        assert set(payload) == {
            "strategy",
            "symbol",
            "equity_curve",
            "metrics",
            "benchmark",
        }
        assert payload["strategy"] == "S"
        assert payload["symbol"] == "XYZ"
        assert set(payload["equity_curve"][0]) == {"date", "equity", "target_weight"}
        assert len(payload["equity_curve"]) == 5

    def test_to_dict_contains_only_plain_numbers(self):
        import json

        payload = run_backtest("S", "XYZ", DATES, CLOSES, np.ones(5)).to_dict()

        json.dumps(payload, allow_nan=False)

    def test_mismatched_dates_are_rejected(self):
        with pytest.raises(ValueError, match="same length"):
            run_backtest("S", "XYZ", DATES[:3], CLOSES, np.ones(5))

    def test_default_cost_is_the_engines(self):
        default = run_backtest("S", "X", DATES, CLOSES, np.ones(5))
        explicit = run_backtest(
            "S", "X", DATES, CLOSES, np.ones(5), CostModel(5.0, 2.0)
        )

        np.testing.assert_allclose(default.equity, explicit.equity)
