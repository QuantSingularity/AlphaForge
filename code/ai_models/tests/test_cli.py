import json

import pytest
from ai_models import __version__
from ai_models.cli import build_parser, main


def run(capsys, *argv):
    code = main([str(a) for a in argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.fixture
def trained(data_dir, tmp_path, capsys):
    artifacts = tmp_path / "artifacts"
    code, _, _ = run(
        capsys,
        "train",
        "--symbols",
        "AAA",
        "BBB",
        "--data-dir",
        data_dir,
        "--artifacts-dir",
        artifacts,
        "--json",
    )
    assert code == 0
    return artifacts


class TestTrain:
    def test_trains_and_stores_the_requested_symbols(self, trained, capsys):
        code, out, _ = run(capsys, "list", "--artifacts-dir", trained, "--json")

        rows = json.loads(out)
        assert code == 0
        assert [r["symbol"] for r in rows] == ["AAA", "BBB"]
        assert all(r["rows"] == 500 for r in rows)

    def test_json_output_is_the_evaluation_summary(self, data_dir, tmp_path, capsys):
        code, out, _ = run(
            capsys,
            "train",
            "--symbols",
            "AAA",
            "--data-dir",
            data_dir,
            "--artifacts-dir",
            tmp_path / "a",
            "--json",
        )

        report = json.loads(out)[0]
        assert code == 0
        assert report["symbol"] == "AAA"
        assert {
            "classification",
            "volatility",
            "strategy",
            "volatility_target",
            "benchmark",
        } <= set(report)

    def test_table_output_has_a_row_per_symbol(self, data_dir, tmp_path, capsys):
        code, out, err = run(
            capsys, "train", "--data-dir", data_dir, "--artifacts-dir", tmp_path / "a"
        )

        assert code == 0
        for symbol in ("AAA", "BBB", "CCC"):
            assert symbol in out
            assert f"trained {symbol}" in err
        assert "sharpe" in out.lower()

    def test_defaults_to_every_symbol_in_the_data_directory(
        self, data_dir, tmp_path, capsys
    ):
        run(
            capsys,
            "train",
            "--data-dir",
            data_dir,
            "--artifacts-dir",
            tmp_path / "a",
            "--no-evaluate",
        )

        _, out, _ = run(capsys, "list", "--artifacts-dir", tmp_path / "a", "--json")
        assert [r["symbol"] for r in json.loads(out)] == ["AAA", "BBB", "CCC"]

    def test_no_evaluate_stores_models_without_a_report(
        self, data_dir, tmp_path, capsys
    ):
        code, out, _ = run(
            capsys,
            "train",
            "--symbols",
            "AAA",
            "--data-dir",
            data_dir,
            "--artifacts-dir",
            tmp_path / "a",
            "--no-evaluate",
            "--json",
        )

        stored = json.loads(out)
        assert code == 0
        assert stored[0]["symbol"] == "AAA" and "version" in stored[0]

    def test_symbols_are_case_insensitive(self, data_dir, tmp_path, capsys):
        code, _, _ = run(
            capsys,
            "train",
            "--symbols",
            "aaa",
            "--data-dir",
            data_dir,
            "--artifacts-dir",
            tmp_path / "a",
            "--no-evaluate",
        )

        assert code == 0

    def test_an_unknown_symbol_is_a_clean_error(self, data_dir, tmp_path, capsys):
        code, out, err = run(
            capsys,
            "train",
            "--symbols",
            "ZZZ",
            "--data-dir",
            data_dir,
            "--artifacts-dir",
            tmp_path / "a",
        )

        assert code == 1
        assert out == ""
        assert "no data file for symbol ZZZ" in err

    def test_an_empty_data_directory_is_a_clean_error(self, tmp_path, capsys):
        (tmp_path / "empty").mkdir()

        code, _, err = run(
            capsys,
            "train",
            "--data-dir",
            tmp_path / "empty",
            "--artifacts-dir",
            tmp_path / "a",
        )

        assert code == 1
        assert "no CSV files found" in err

    def test_an_invalid_option_value_is_a_clean_error(self, data_dir, tmp_path, capsys):
        code, _, err = run(
            capsys,
            "train",
            "--symbols",
            "AAA",
            "--data-dir",
            data_dir,
            "--artifacts-dir",
            tmp_path / "a",
            "--horizon",
            "0",
        )

        assert code == 1
        assert "horizon" in err


class TestEvaluate:
    def test_evaluates_without_storing_anything(self, data_dir, tmp_path, capsys):
        artifacts = tmp_path / "a"

        code, out, _ = run(
            capsys,
            "evaluate",
            "--symbols",
            "AAA",
            "--data-dir",
            data_dir,
            "--artifacts-dir",
            artifacts,
            "--json",
        )

        assert code == 0
        assert json.loads(out)[0]["symbol"] == "AAA"
        assert not artifacts.exists()

    def test_the_model_and_target_volatility_options_apply(self, data_dir, capsys):
        code, out, _ = run(
            capsys,
            "evaluate",
            "--symbols",
            "AAA",
            "--data-dir",
            data_dir,
            "--model",
            "gradient_boosting",
            "--target-volatility",
            "0.2",
            "--json",
        )

        assert code == 0
        assert json.loads(out)[0]["symbol"] == "AAA"


class TestListAndPredict:
    def test_list_with_nothing_trained(self, tmp_path, capsys):
        code, out, _ = run(capsys, "list", "--artifacts-dir", tmp_path / "none")

        assert code == 0
        assert "no trained models" in out

    def test_list_prints_one_line_per_model(self, trained, capsys):
        _, out, _ = run(capsys, "list", "--artifacts-dir", trained)

        assert out.count("\n") == 2
        assert "rows=500" in out

    def test_predict_prints_a_summary_line(self, trained, data_dir, capsys):
        code, out, _ = run(
            capsys,
            "predict",
            "--symbol",
            "aaa",
            "--data-dir",
            data_dir,
            "--artifacts-dir",
            trained,
        )

        assert code == 0
        assert out.startswith("AAA ")
        assert "P(up)=" in out and "regime=" in out

    def test_predict_json_includes_the_explanation(self, trained, data_dir, capsys):
        code, out, _ = run(
            capsys,
            "predict",
            "--symbol",
            "AAA",
            "--data-dir",
            data_dir,
            "--artifacts-dir",
            trained,
            "--json",
        )

        payload = json.loads(out)
        assert code == 0
        assert 0.0 < payload["prob_up"] < 1.0
        assert payload["contributions"]

    def test_predict_without_a_trained_model(self, data_dir, tmp_path, capsys):
        code, _, err = run(
            capsys,
            "predict",
            "--symbol",
            "AAA",
            "--data-dir",
            data_dir,
            "--artifacts-dir",
            tmp_path / "a",
        )

        assert code == 1
        assert "no trained model for AAA" in err

    def test_predict_requires_a_symbol(self, capsys):
        with pytest.raises(SystemExit) as exit_info:
            main(["predict"])

        assert exit_info.value.code == 2


class TestParser:
    def test_a_command_is_required(self, capsys):
        with pytest.raises(SystemExit) as exit_info:
            main([])

        assert exit_info.value.code == 2

    def test_reports_its_version(self, capsys):
        with pytest.raises(SystemExit) as exit_info:
            main(["--version"])

        assert exit_info.value.code == 0
        assert __version__ in capsys.readouterr().out

    def test_the_model_choices_are_validated(self):
        with pytest.raises(SystemExit):
            build_parser().parse_args(["train", "--model", "svm"])
