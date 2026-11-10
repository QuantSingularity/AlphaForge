import numpy as np
import pandas as pd
import pytest
from ai_models.exceptions import DataError, InsufficientDataError
from ai_models.features import (
    FEATURE_COLUMNS,
    WARMUP_ROWS,
    compute_features,
    forward_returns,
    latest_features,
    make_dataset,
)
from ai_models.testing import synthetic_bars


def make_bars(closes, highs=None, lows=None, opens=None, volume=1000.0):
    closes = np.asarray(closes, dtype=float)
    frame = pd.DataFrame(
        {
            "open": closes if opens is None else opens,
            "high": closes * 1.01 if highs is None else highs,
            "low": closes * 0.99 if lows is None else lows,
            "close": closes,
            "volume": np.full(closes.size, volume) if np.isscalar(volume) else volume,
        },
        index=pd.bdate_range("2020-01-01", periods=closes.size, name="date"),
    )
    return frame.astype(float)


class TestShape:
    def test_has_the_documented_columns_in_order(self, bars):
        features = compute_features(bars)

        assert tuple(features.columns) == FEATURE_COLUMNS
        assert features.index.equals(bars.index)

    def test_warm_up_is_the_only_source_of_missing_values(self, bars):
        features = compute_features(bars)

        complete = features.notna().all(axis=1)
        first_complete = int(np.argmax(complete.to_numpy()))
        assert first_complete <= WARMUP_ROWS
        assert complete.iloc[first_complete:].all()
        assert not complete.iloc[:first_complete].any()

    def test_values_are_finite(self, bars):
        values = compute_features(bars).dropna().to_numpy()

        assert np.isfinite(values).all()


class TestNoLookAhead:
    """A feature at time t may only use bars up to t."""

    @pytest.mark.parametrize("cut", [80, 150, 300, 599])
    def test_truncating_the_history_does_not_change_earlier_rows(self, bars, cut):
        full = compute_features(bars)
        truncated = compute_features(bars.iloc[:cut])

        pd.testing.assert_frame_equal(truncated, full.iloc[:cut], check_freq=False)

    def test_changing_future_bars_does_not_change_earlier_rows(self, bars):
        cut = 300
        altered = bars.copy()
        rng = np.random.default_rng(0)
        for column in ["open", "high", "low", "close"]:
            altered.iloc[cut:, altered.columns.get_loc(column)] *= rng.uniform(
                0.5, 2.0, len(altered) - cut
            )
        altered.iloc[cut:, altered.columns.get_loc("volume")] *= 10.0

        before = compute_features(bars).iloc[:cut]
        after = compute_features(altered).iloc[:cut]

        pd.testing.assert_frame_equal(before, after, check_freq=False)

    def test_the_label_does_use_the_future_but_the_features_do_not(self, bars):
        dataset = make_dataset(bars, 1)
        features = compute_features(bars).loc[dataset.features.index]

        pd.testing.assert_frame_equal(dataset.features, features, check_freq=False)


class TestKnownValues:
    def test_one_day_return_is_the_log_difference(self):
        bars = make_bars([100.0, 110.0, 99.0] + [100.0] * 80)

        features = compute_features(bars)

        assert features["ret_1"].iloc[1] == pytest.approx(np.log(110 / 100))
        assert features["ret_1"].iloc[2] == pytest.approx(np.log(99 / 110))

    def test_multi_day_returns_span_the_window(self, bars):
        features = compute_features(bars)
        log_close = np.log(bars["close"])

        assert features["ret_21"].iloc[100] == pytest.approx(
            log_close.iloc[100] - log_close.iloc[79]
        )

    def test_volatility_is_the_annualised_sample_std_of_returns(self, bars):
        features = compute_features(bars)
        returns = np.log(bars["close"]).diff()

        expected = returns.iloc[96:101].std(ddof=1) * np.sqrt(252)
        assert features["vol_5"].iloc[100] == pytest.approx(expected)

    def test_rsi_is_100_in_a_pure_uptrend(self):
        bars = make_bars(np.linspace(100, 200, 100))

        assert compute_features(bars)["rsi_14"].iloc[-1] == pytest.approx(100.0)

    def test_rsi_is_zero_in_a_pure_downtrend(self):
        bars = make_bars(np.linspace(200, 100, 100))

        assert compute_features(bars)["rsi_14"].iloc[-1] == pytest.approx(0.0)

    def test_rsi_is_50_when_nothing_moves(self):
        bars = make_bars(np.full(100, 50.0))

        assert compute_features(bars)["rsi_14"].iloc[-1] == pytest.approx(50.0)

    def test_rsi_stays_within_its_range(self, bars):
        rsi = compute_features(bars)["rsi_14"].dropna()

        assert rsi.between(0.0, 100.0).all()

    def test_bollinger_position_is_centred_when_price_is_flat(self):
        bars = make_bars(np.full(100, 80.0))

        assert compute_features(bars)["bb_pctb"].iloc[-1] == pytest.approx(0.5)

    def test_bollinger_position_is_high_after_a_spike(self):
        closes = np.concatenate([np.full(60, 100.0), [130.0]])

        value = compute_features(make_bars(closes))["bb_pctb"].iloc[-1]

        assert value > 0.9

    def test_distance_from_the_average_is_zero_in_a_flat_market(self):
        bars = make_bars(np.full(100, 70.0))

        features = compute_features(bars)

        assert features["sma_dist_20"].iloc[-1] == pytest.approx(0.0)
        assert features["sma_dist_50"].iloc[-1] == pytest.approx(0.0)

    def test_gap_is_open_over_the_previous_close(self):
        closes = np.full(80, 100.0)
        opens = closes.copy()
        opens[70] = 103.0

        features = compute_features(make_bars(closes, opens=opens))

        assert features["gap"].iloc[70] == pytest.approx(0.03)
        assert features["gap"].iloc[71] == pytest.approx(0.0)

    def test_range_is_high_minus_low_over_close(self):
        bars = make_bars(
            np.full(80, 100.0), highs=np.full(80, 102.0), lows=np.full(80, 97.0)
        )

        assert compute_features(bars)["range_pct"].iloc[-1] == pytest.approx(0.05)

    def test_atr_equals_the_range_when_bars_do_not_gap(self):
        bars = make_bars(
            np.full(80, 100.0), highs=np.full(80, 102.0), lows=np.full(80, 97.0)
        )

        assert compute_features(bars)["atr_pct"].iloc[-1] == pytest.approx(0.05)

    def test_atr_includes_overnight_gaps(self):
        closes = np.full(80, 100.0)
        highs = np.full(80, 100.5)
        lows = np.full(80, 99.5)
        highs[-1], lows[-1] = 111.0, 110.0  # gaps up 10, intraday range only 1
        quiet = compute_features(
            make_bars(closes, highs=np.full(80, 100.5), lows=np.full(80, 99.5))
        )
        gapped = compute_features(make_bars(closes, highs=highs, lows=lows))

        # The gap bar has a true range of 11 (not 1). Wilder smoothing gives that
        # one bar a weight of 1/14: (13 * 1 + 11) / 14 percent of a price of 100.
        assert quiet["atr_pct"].iloc[-1] == pytest.approx(0.01)
        assert gapped["atr_pct"].iloc[-1] == pytest.approx(
            (13 * 1.0 + 11.0) / 14 / 100.0
        )

    @pytest.mark.parametrize(
        ("close", "expected"),
        [(110.0, 1.0), (90.0, -1.0), (100.0, 0.0)],
    )
    def test_close_location_runs_from_minus_one_to_one(self, close, expected):
        closes = np.full(80, 100.0)
        closes[-1] = close
        bars = make_bars(closes, highs=np.full(80, 110.0), lows=np.full(80, 90.0))

        assert compute_features(bars)["close_loc"].iloc[-1] == pytest.approx(expected)

    def test_close_location_is_zero_for_a_bar_with_no_range(self):
        bars = make_bars(
            np.full(80, 100.0), highs=np.full(80, 100.0), lows=np.full(80, 100.0)
        )

        assert compute_features(bars)["close_loc"].iloc[-1] == 0.0

    def test_volume_z_is_zero_for_constant_volume(self):
        bars = make_bars(np.full(80, 100.0), volume=5000.0)

        assert compute_features(bars)["volume_z"].iloc[-1] == 0.0

    def test_volume_z_is_high_after_a_volume_spike(self):
        volume = np.full(80, 1000.0)
        volume[-1] = 50_000.0
        volume += np.random.default_rng(0).uniform(0, 50, 80)

        assert (
            compute_features(make_bars(np.full(80, 100.0), volume=volume))[
                "volume_z"
            ].iloc[-1]
            > 3
        )

    def test_drawdown_is_the_fall_from_the_recent_high(self):
        closes = np.concatenate([np.linspace(100, 150, 70), [120.0]])

        value = compute_features(make_bars(closes))["drawdown_63"].iloc[-1]

        assert value == pytest.approx(120.0 / 150.0 - 1.0)

    def test_macd_histogram_is_positive_when_momentum_accelerates(self):
        closes = np.concatenate(
            [np.full(60, 100.0), 100.0 * np.exp(0.01 * np.arange(1, 21))]
        )

        assert compute_features(make_bars(closes))["macd_hist"].iloc[-1] > 0

    def test_vol_ratio_compares_short_and_long_volatility(self, bars):
        features = compute_features(bars).dropna()

        np.testing.assert_allclose(
            features["vol_ratio"], features["vol_5"] / features["vol_21"]
        )


class TestForwardReturns:
    def test_is_the_return_to_the_close_h_bars_later(self):
        bars = make_bars([100.0, 110.0, 99.0, 105.0])

        one = forward_returns(bars, 1)
        two = forward_returns(bars, 2)

        assert one.iloc[0] == pytest.approx(0.10)
        assert one.iloc[1] == pytest.approx(99 / 110 - 1)
        assert two.iloc[0] == pytest.approx(99 / 100 - 1)

    def test_the_final_rows_are_unknown(self):
        bars = make_bars(np.linspace(100, 110, 10))

        outcome = forward_returns(bars, 3)

        assert outcome.iloc[-3:].isna().all()
        assert outcome.iloc[:-3].notna().all()

    def test_horizon_must_be_positive(self, bars):
        with pytest.raises(ValueError, match="horizon"):
            forward_returns(bars, 0)


class TestMakeDataset:
    def test_aligns_features_labels_and_outcomes(self, bars):
        dataset = make_dataset(bars, 1)

        assert dataset.features.index.equals(dataset.target.index)
        assert dataset.features.index.equals(dataset.forward_return.index)
        assert len(dataset) == len(dataset.features)
        assert tuple(dataset.features.columns) == FEATURE_COLUMNS

    def test_label_is_one_for_a_positive_forward_return(self, bars):
        dataset = make_dataset(bars, 1)

        np.testing.assert_array_equal(
            dataset.target.to_numpy(), (dataset.forward_return > 0).astype(int)
        )

    def test_drops_warm_up_rows_and_the_unlabelled_tail(self, bars):
        dataset = make_dataset(bars, 1)

        first_complete = int(
            np.argmax(compute_features(bars).notna().all(axis=1).to_numpy())
        )
        assert len(dataset) == len(bars) - first_complete - 1
        assert dataset.features.index[-1] == bars.index[-2]

    def test_a_longer_horizon_drops_more_tail_rows(self, bars):
        assert len(make_dataset(bars, 5)) == len(make_dataset(bars, 1)) - 4

    def test_labels_match_the_horizon(self, bars):
        dataset = make_dataset(bars, 5)

        close = bars["close"]
        date = dataset.features.index[10]
        position = bars.index.get_loc(date)
        assert dataset.forward_return.loc[date] == pytest.approx(
            close.iloc[position + 5] / close.iloc[position] - 1
        )

    def test_too_few_bars_raise(self):
        with pytest.raises(InsufficientDataError, match="not enough"):
            make_dataset(synthetic_bars(n=40), 1)


class TestLatestFeatures:
    def test_returns_the_last_complete_rows(self, bars):
        latest = latest_features(bars, 3)

        assert len(latest) == 3
        assert latest.index[-1] == bars.index[-1]
        assert latest.notna().all().all()

    def test_matches_the_full_feature_frame(self, bars):
        pd.testing.assert_frame_equal(
            latest_features(bars, 5), compute_features(bars).iloc[-5:], check_freq=False
        )

    def test_does_not_need_a_label(self, bars):
        assert latest_features(bars.iloc[:-1], 1).index[-1] == bars.index[-2]

    def test_too_few_bars_raise(self):
        with pytest.raises(InsufficientDataError, match="need"):
            latest_features(synthetic_bars(n=50), 1)

    def test_rows_must_be_positive(self, bars):
        with pytest.raises(ValueError, match="rows"):
            latest_features(bars, 0)


class TestInputValidation:
    def test_missing_columns_raise(self, bars):
        with pytest.raises(DataError, match="missing columns"):
            compute_features(bars.drop(columns=["volume"]))

    def test_unsorted_bars_raise(self, bars):
        with pytest.raises(DataError, match="sorted"):
            compute_features(bars.iloc[::-1])
