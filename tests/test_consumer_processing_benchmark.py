from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.cli import _default_arguments, main  # noqa: E402
from benchmarks.consumer_processing import (  # noqa: E402
    BENCHMARK,
    DEFAULT_LOOP_MESSAGES,
    DEFAULT_REPEATS,
    PREFETCH_VALUES,
    TARGET_SIZES,
    MessageFixture,
    PerMessageCase,
    build_consumer_loop_matrix,
    build_fixture,
    build_per_message_matrix,
    calculate_per_message_result,
    render_html,
    run_consumer_loop_benchmarks,
    run_per_message_benchmarks,
    write_html_report,
)
from benchmarks.reporting import collect_environment  # noqa: E402

_ONE_PER_SIZE = {size: 1 for size in TARGET_SIZES.values()}


@pytest.mark.parametrize("input_kind", ["canonical", "configured-alias"])
@pytest.mark.parametrize(("target_label", "target_bytes"), TARGET_SIZES.items())
def test_fixtures_match_exact_actual_body_sizes(
    input_kind: str,
    target_label: str,
    target_bytes: int,
) -> None:
    fixture = build_fixture(input_kind, target_bytes)  # type: ignore[arg-type]

    assert len(fixture.body) == target_bytes
    assert fixture.actual_message_bytes == target_bytes
    assert target_label in TARGET_SIZES


def test_per_message_matrix_is_complete_and_bounded() -> None:
    matrix = build_per_message_matrix(_ONE_PER_SIZE)
    identities = {(case.profile, case.input_kind, case.target_bytes) for case in matrix}

    assert len(matrix) == 16
    assert len(identities) == 16
    assert {case.profile for case in matrix} == {
        "minimal",
        "observability-enabled",
    }
    assert {case.input_kind for case in matrix} == {
        "canonical",
        "configured-alias",
    }
    assert {case.target_bytes for case in matrix} == set(TARGET_SIZES.values())


def test_consumer_loop_matrix_is_canonical_and_covers_prefetch() -> None:
    matrix = build_consumer_loop_matrix(_ONE_PER_SIZE)
    identities = {(case.profile, case.target_bytes, case.prefetch) for case in matrix}

    assert len(matrix) == 24
    assert len(identities) == 24
    assert {case.prefetch for case in matrix} == set(PREFETCH_VALUES)
    assert all(case.message_count == case.prefetch for case in matrix)
    assert {case.target_bytes for case in matrix} == set(TARGET_SIZES.values())


def test_consumer_loop_defaults_are_high_cardinality_and_size_aware() -> None:
    matrix = build_consumer_loop_matrix()

    assert DEFAULT_REPEATS == 5
    assert DEFAULT_LOOP_MESSAGES == {
        1_024: 8_192,
        16_384: 2_048,
        131_072: 256,
        1_048_576: 64,
    }
    assert {
        case.target_bytes: case.message_count for case in matrix if case.profile == "minimal" and case.prefetch == 32
    } == DEFAULT_LOOP_MESSAGES


def test_per_message_uses_real_path_and_validates_success_counts() -> None:
    results = run_per_message_benchmarks(
        repeats=1,
        iterations_by_size=_ONE_PER_SIZE,
    )

    assert len(results) == 16
    assert all(result.actual_message_bytes == result.case.target_bytes for result in results)
    assert all(result.total_messages == 1 for result in results)
    assert all(result.handler_count == 1 for result in results)
    assert all(result.ack_count == 1 for result in results)
    assert all(result.reject_count == 0 for result in results)
    assert all(result.median_ns_per_message > 0 for result in results)
    assert all(result.messages_per_second > 0 for result in results)
    assert all(result.throughput_mib_per_second > 0 for result in results)
    assert {result.observation_count for result in results if result.case.profile == "minimal"} == {0}
    assert all(result.observation_count > 0 for result in results if result.case.profile == "observability-enabled")


def test_per_message_metric_calculations() -> None:
    case = PerMessageCase(
        profile="minimal",
        input_kind="canonical",
        target_label="1 KB",
        target_bytes=1_024,
        iterations=2,
    )
    fixture = MessageFixture(
        body=b"x" * 1_024,
        actual_message_bytes=1_024,
        input_kind="canonical",
    )

    result = calculate_per_message_result(
        case,
        fixture,
        (10_000.0, 14_000.0, 12_000.0),
        repeats=3,
        handler_count=6,
        ack_count=6,
        reject_count=0,
        observation_count=0,
    )

    assert result.median_ns_per_message == 12_000.0
    assert result.median_absolute_deviation_ns == 2_000.0
    assert result.messages_per_second == pytest.approx(1_000_000_000 / 12_000)
    assert result.throughput_mib_per_second == pytest.approx(1_024 / (1024 * 1024) / (12_000 / 1_000_000_000))
    assert result.total_messages == 6


def test_consumer_loop_terminates_and_reports_prefetch_concurrency_and_counts() -> None:
    results = run_consumer_loop_benchmarks(
        repeats=1,
        message_counts_by_size=_ONE_PER_SIZE,
    )

    assert len(results) == 24
    assert all(result.actual_message_bytes == result.case.target_bytes for result in results)
    assert all(
        result.total_bytes_per_sample == result.actual_message_bytes * result.case.message_count for result in results
    )
    assert all(result.handler_count == result.case.message_count for result in results)
    assert all(result.ack_count == result.case.message_count for result in results)
    assert all(result.reject_count == 0 for result in results)
    assert all(result.peak_concurrency == result.case.prefetch for result in results)
    assert all(result.median_total_duration_ns > 0 for result in results)
    assert all(
        result.median_ns_per_message == pytest.approx(result.median_total_duration_ns / result.case.message_count)
        for result in results
    )
    assert all(
        result.messages_per_second == pytest.approx(1_000_000_000 / result.median_ns_per_message) for result in results
    )
    assert all(
        result.throughput_mib_per_second
        == pytest.approx(result.actual_message_bytes / (1024 * 1024) / (result.median_ns_per_message / 1_000_000_000))
        for result in results
    )


def test_benchmarks_reuse_event_loops_without_asyncio_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_asyncio_run(_awaitable: object) -> None:
        raise AssertionError("asyncio.run() must not be used per operation")

    monkeypatch.setattr(asyncio, "run", forbidden_asyncio_run)

    assert (
        len(
            run_per_message_benchmarks(
                repeats=1,
                iterations_by_size=_ONE_PER_SIZE,
            )
        )
        == 16
    )
    assert (
        len(
            run_consumer_loop_benchmarks(
                repeats=1,
                message_counts_by_size=_ONE_PER_SIZE,
            )
        )
        == 24
    )


def test_html_contains_both_measurements_methodology_matrix_counts_and_metadata(
    tmp_path: Path,
) -> None:
    per_message_results = run_per_message_benchmarks(
        repeats=1,
        iterations_by_size=_ONE_PER_SIZE,
    )
    loop_results = run_consumer_loop_benchmarks(
        repeats=1,
        message_counts_by_size=_ONE_PER_SIZE,
    )
    environment = collect_environment(
        datetime(2026, 7, 30, 12, 0, tzinfo=UTC),
        extra={"Benchmark": "consumer-processing"},
    )
    report_path = write_html_report(
        tmp_path / "nested" / "consumer-processing.html",
        per_message_results,
        loop_results,
        environment,
        measurement="all",
    )
    rendered = render_html(
        per_message_results,
        loop_results,
        environment,
        measurement="all",
    )

    assert report_path.read_text(encoding="utf-8") == rendered
    if os.name == "posix":
        assert report_path.stat().st_mode & 0o777 == 0o644
    assert "<!doctype html>" in rendered
    assert "Measurement 1 — per-message" in rendered
    assert "Measurement 2 — consumer-loop" in rendered
    assert "Methodology" in rendered
    assert "Matrix and configuration" in rendered
    assert "Bottleneck conclusions" in rendered
    assert "Environment and package metadata" in rendered
    assert "Actual bytes" in rendered
    assert "Peak concurrency" in rendered
    assert "Handlers" in rendered
    assert "Acks" in rendered
    assert "Rejects" in rendered
    assert "2026-07-30T12:00:00Z" in rendered


def _quick_cli_args(output_path: Path) -> list[str]:
    args = ["--repeats", "1"]
    for label in TARGET_SIZES:
        args.extend(["--iterations", f"{label}=1"])
        args.extend(["--loop-messages", f"{label}=1"])
    args.extend(["--output", str(output_path)])
    return args


@pytest.mark.parametrize(
    ("measurement", "expected_count"),
    [
        ("per-message", 16),
        ("consumer-loop", 24),
        ("all", 40),
    ],
)
def test_cli_dispatches_every_measurement_choice(
    measurement: str,
    expected_count: int,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output_path = tmp_path / f"{measurement}.html"

    assert (
        main(
            [
                "run",
                "consumer-processing",
                "--measurement",
                measurement,
                *_quick_cli_args(output_path),
            ]
        )
        == 0
    )

    assert output_path.is_file()
    assert f"Completed consumer-processing: {expected_count} measurements" in capsys.readouterr().out


def test_cli_default_and_run_all_arguments_select_all(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output_path = tmp_path / "default-all.html"

    assert (
        main(
            [
                "run",
                "consumer-processing",
                *_quick_cli_args(output_path),
            ]
        )
        == 0
    )
    assert "Completed consumer-processing: 40 measurements" in capsys.readouterr().out

    args = _default_arguments(BENCHMARK)
    assert isinstance(args, argparse.Namespace)
    assert args.measurement == "all"
    assert args.repeats == DEFAULT_REPEATS
    assert args.output == Path("reports/consumer-processing.html")


@pytest.mark.parametrize(
    "argv",
    [
        ["run", "consumer-processing", "--measurement", "invalid"],
        ["run", "consumer-processing", "--repeats", "0"],
    ],
)
def test_cli_rejects_invalid_consumer_processing_options(argv: list[str]) -> None:
    if argv[-1] == "0":
        with pytest.raises(ValueError, match="Repeats must be positive"):
            main(argv)
    else:
        with pytest.raises(SystemExit) as error:
            main(argv)
        assert error.value.code == 2
