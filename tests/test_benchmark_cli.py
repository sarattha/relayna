from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.cli import main  # noqa: E402
from benchmarks.registry import (  # noqa: E402
    BenchmarkDefinition,
    BenchmarkOutcome,
    _validate_definitions,
    registered_benchmarks,
)


def test_registry_has_unique_valid_repository_relative_definitions() -> None:
    definitions = registered_benchmarks()

    assert [definition.name for definition in definitions] == ["envelope-serialization", "redis-storage-cpu"]
    assert all(not definition.default_output.is_absolute() for definition in definitions)


def test_registry_rejects_duplicate_invalid_and_absolute_definitions() -> None:
    def add_arguments(_parser: argparse.ArgumentParser) -> None:
        pass

    def run(_args: argparse.Namespace) -> BenchmarkOutcome:
        return BenchmarkOutcome(artifacts=(), measurement_count=0)

    valid = BenchmarkDefinition("valid-name", "Valid.", Path("reports/valid.html"), add_arguments, run)

    with pytest.raises(ValueError, match="Duplicate benchmark name"):
        _validate_definitions((valid, valid))
    with pytest.raises(ValueError, match="lowercase kebab-case"):
        _validate_definitions(
            (BenchmarkDefinition("Invalid_Name", "Invalid.", Path("reports/invalid.html"), add_arguments, run),)
        )
    with pytest.raises(ValueError, match="repository-relative"):
        _validate_definitions(
            (BenchmarkDefinition("absolute", "Absolute.", Path("/tmp/absolute.html"), add_arguments, run),)
        )


def test_cli_lists_registered_benchmarks(capsys) -> None:
    assert main(["list"]) == 0

    output = capsys.readouterr().out
    assert "NAME" in output
    assert "envelope-serialization" in output
    assert "reports/envelope-microbenchmarks.html" in output
    assert "redis-storage-cpu" in output
    assert "reports/redis-storage-cpu-microbenchmarks.html" in output


def test_cli_runs_envelope_benchmark_with_specific_options(tmp_path, capsys) -> None:
    output_path = tmp_path / "cli-report.html"

    exit_code = main(
        [
            "run",
            "envelope-serialization",
            "--repeats",
            "1",
            "--iterations",
            "1 KB=1",
            "--iterations",
            "16 KB=1",
            "--iterations",
            "128 KB=1",
            "--iterations",
            "1 MB=1",
            "--output",
            str(output_path),
        ]
    )

    assert exit_code == 0
    assert output_path.is_file()
    assert "Completed envelope-serialization: 32 measurements" in capsys.readouterr().out


def test_cli_rejects_invalid_benchmark_specific_options() -> None:
    with pytest.raises(SystemExit) as error:
        main(["run", "envelope-serialization", "--repeats", "0"])

    assert error.value.code == 2


def test_cli_run_all_dispatches_canonical_defaults(monkeypatch, tmp_path, capsys) -> None:
    received: list[argparse.Namespace] = []

    def add_arguments(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--output", type=Path, default=tmp_path / "fake.html")

    def run(args: argparse.Namespace) -> BenchmarkOutcome:
        received.append(args)
        args.output.write_text("complete", encoding="utf-8")
        return BenchmarkOutcome(artifacts=(args.output,), measurement_count=3)

    definition = BenchmarkDefinition(
        name="fake-benchmark",
        summary="Fake benchmark.",
        default_output=Path("reports/fake.html"),
        add_arguments=add_arguments,
        run=run,
    )
    monkeypatch.setattr("benchmarks.cli.registered_benchmarks", lambda: (definition,))

    assert main(["run-all"]) == 0
    assert len(received) == 1
    assert received[0].output.read_text(encoding="utf-8") == "complete"
    assert "Completed fake-benchmark: 3 measurements" in capsys.readouterr().out


def test_cli_rejects_a_runner_that_does_not_write_its_artifact(monkeypatch, tmp_path) -> None:
    def add_arguments(_parser: argparse.ArgumentParser) -> None:
        pass

    def run(_args: argparse.Namespace) -> BenchmarkOutcome:
        return BenchmarkOutcome(artifacts=(tmp_path / "missing.html",), measurement_count=1)

    definition = BenchmarkDefinition(
        name="missing-artifact",
        summary="Missing artifact.",
        default_output=Path("reports/missing.html"),
        add_arguments=add_arguments,
        run=run,
    )
    monkeypatch.setattr("benchmarks.cli.registered_benchmarks", lambda: (definition,))

    with pytest.raises(RuntimeError, match="did not write expected artifacts"):
        main(["run-all"])
