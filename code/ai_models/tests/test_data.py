import numpy as np
import pandas as pd
import pytest
from ai_models.data import (
    fingerprint,
    list_symbols,
    load_symbol,
    normalise_symbol,
    read_bars,
    symbol_path,
    validate_bars,
)
from ai_models.exceptions import DataError, DataNotFoundError
from ai_models.testing import synthetic_bars, write_csv

HEADER = "date,open,high,low,close,volume\n"
ROW = "2024-01-02,10.0,11.0,9.0,10.5,1000\n"


def write(tmp_path, text, name="X.csv"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


class TestReadBars:
    def test_reads_the_engine_csv_format(self, tmp_path):
        path = write(tmp_path, HEADER + ROW + "2024-01-03,10.5,12.0,10.0,11.5,2000\n")

        bars = read_bars(path)

        assert list(bars.columns) == ["open", "high", "low", "close", "volume"]
        assert bars.index.name == "date"
        assert bars.index[0] == pd.Timestamp("2024-01-02")
        assert bars["close"].tolist() == [10.5, 11.5]
        assert (bars.dtypes == float).all()

    def test_column_names_are_case_insensitive(self, tmp_path):
        text = "Date,Open,High,Low,Close,Volume\n2024-01-02,10,11,9,10.5,1000\n"

        assert len(read_bars(write(tmp_path, text))) == 1

    def test_rows_are_sorted_by_date(self, tmp_path):
        path = write(
            tmp_path, HEADER + "2024-01-03,10,11,9,10,1\n2024-01-02,10,11,9,10,1\n"
        )

        bars = read_bars(path)

        assert bars.index.is_monotonic_increasing

    def test_round_trips_synthetic_data(self, tmp_path):
        original = synthetic_bars(n=50, seed=3)
        path = write_csv(original, tmp_path / "S.csv")

        loaded = read_bars(path)

        pd.testing.assert_frame_equal(loaded, original.round(4), check_freq=False)

    @pytest.mark.parametrize(
        ("text", "message"),
        [
            ("", "parse"),
            (HEADER, "no rows"),
            ("date,open,high,low,close\n2024-01-02,1,2,0.5,1.5\n", "volume"),
            ("open,high,low,close,volume\n1,2,0.5,1.5,10\n", "date"),
            (HEADER + "02/01/2024,10,11,9,10,1\n", "dates"),
            (HEADER + "2024-01-02,abc,11,9,10,1\n", "non-numeric"),
            (HEADER + "2024-01-02,,11,9,10,1\n", "non-finite"),
            (HEADER + "2024-01-02,10,11,9,0,1\n", "non-positive"),
            (HEADER + "2024-01-02,10,11,9,10,-5\n", "negative volume"),
            (HEADER + "2024-01-02,10,9,11,10,1\n", "high below low"),
            (HEADER + ROW + ROW, "duplicate"),
        ],
    )
    def test_malformed_files_raise_a_data_error(self, tmp_path, text, message):
        with pytest.raises(DataError, match=message):
            read_bars(write(tmp_path, text))

    def test_missing_file_raises_a_not_found_error_without_the_directory(
        self, tmp_path
    ):
        with pytest.raises(DataNotFoundError, match="not found") as raised:
            read_bars(tmp_path / "missing.csv")

        assert str(tmp_path) not in str(raised.value)

    def test_a_close_outside_the_range_is_reported_but_tolerated(self, tmp_path):
        text = HEADER + "2024-01-02,10,11,9,12,1\n"

        bars = read_bars(write(tmp_path, text))

        assert "open or close outside the high/low range" in validate_bars(bars)


class TestValidateBars:
    def test_clean_data_has_no_issues(self):
        assert validate_bars(synthetic_bars(n=30)) == []

    def test_reports_every_problem_it_finds(self):
        bars = synthetic_bars(n=30)
        bars.iloc[3, bars.columns.get_loc("high")] = bars.iloc[3]["low"] - 1.0
        bars.iloc[5, bars.columns.get_loc("volume")] = -1.0

        issues = validate_bars(bars)

        assert "high below low" in issues
        assert "negative volume" in issues

    def test_unsorted_index_is_reported(self):
        bars = synthetic_bars(n=10).iloc[::-1]

        assert "dates are not sorted ascending" in validate_bars(bars)

    def test_missing_columns_are_reported(self):
        assert validate_bars(synthetic_bars(n=10).drop(columns=["volume"])) == [
            "missing columns: volume"
        ]

    def test_empty_frame_is_reported(self):
        assert validate_bars(synthetic_bars(n=10).iloc[:0]) == ["no rows"]


class TestSymbols:
    @pytest.mark.parametrize(
        "raw", ["AAPL", "aapl", " spy ", "BRK-B", "BRK.B", "A1", "TLT"]
    )
    def test_valid_symbols_are_upper_cased(self, raw):
        assert normalise_symbol(raw) == raw.strip().upper()

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            " ",
            "../etc/passwd",
            "a/b",
            "a\\b",
            ".hidden",
            "-x",
            "A" * 17,
            "A B",
            "AAPL;rm",
            None,
            7,
        ],
    )
    def test_unsafe_symbols_are_rejected(self, raw):
        with pytest.raises(DataError, match="invalid symbol"):
            normalise_symbol(raw)

    def test_list_symbols_finds_csv_files(self, data_dir):
        assert list_symbols(data_dir) == ["AAA", "BBB", "CCC"]

    def test_list_symbols_ignores_other_files_and_invalid_names(self, data_dir):
        (data_dir / "README.md").write_text("notes")
        (data_dir / "notes.txt").write_text("notes")
        (data_dir / ".hidden.csv").write_text("x")
        (data_dir / "bad name.csv").write_text("x")

        assert list_symbols(data_dir) == ["AAA", "BBB", "CCC"]

    def test_list_symbols_of_a_missing_directory_is_empty(self, tmp_path):
        assert list_symbols(tmp_path / "nope") == []

    def test_symbol_path_matches_case_insensitively(self, data_dir):
        assert symbol_path("aaa", data_dir).name == "AAA.csv"

    def test_unknown_symbol_raises_without_leaking_the_directory(self, data_dir):
        with pytest.raises(
            DataNotFoundError, match="no data file for symbol ZZZ"
        ) as raised:
            symbol_path("ZZZ", data_dir)

        assert str(data_dir) not in str(raised.value)

    def test_path_traversal_never_reaches_the_filesystem(self, data_dir):
        with pytest.raises(DataError, match="invalid symbol"):
            load_symbol("../secret", data_dir)

    def test_load_symbol_returns_validated_bars(self, data_dir):
        bars = load_symbol("bbb", data_dir)

        assert len(bars) == 500
        assert validate_bars(bars) == []


class TestFingerprint:
    def test_is_stable_for_identical_data(self):
        assert fingerprint(synthetic_bars(n=40, seed=1)) == fingerprint(
            synthetic_bars(n=40, seed=1)
        )

    def test_changes_when_any_value_changes(self):
        bars = synthetic_bars(n=40, seed=1)
        altered = bars.copy()
        altered.iloc[10, altered.columns.get_loc("close")] += 0.01

        assert fingerprint(bars) != fingerprint(altered)

    def test_changes_when_dates_change(self):
        bars = synthetic_bars(n=40, seed=1)
        shifted = bars.copy()
        shifted.index = shifted.index + pd.Timedelta(days=1)

        assert fingerprint(bars) != fingerprint(shifted)

    def test_is_a_short_hex_string(self):
        value = fingerprint(synthetic_bars(n=40))

        assert len(value) == 16
        assert int(value, 16) >= 0

    def test_numpy_dtype_does_not_matter(self):
        bars = synthetic_bars(n=40)

        assert fingerprint(bars) == fingerprint(bars.astype(np.float64))
