from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from aio_pika import Message

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.cli import main  # noqa: E402
from benchmarks.publish_preparation import (  # noqa: E402
    TARGET_SIZES,
    BenchmarkCase,
    BenchmarkResult,
    NoOpExchange,
    build_fixture,
    build_matrix,
    load_embedded_results,
    render_html,
    run_benchmarks,
    write_html_report,
)
from benchmarks.reporting import collect_environment  # noqa: E402


def _case(
    message_kind: str,
    input_kind: str,
    topology: str,
    target_label: str,
    target_bytes: int,
) -> BenchmarkCase:
    return BenchmarkCase(
        message_kind=message_kind,  # type: ignore[arg-type]
        input_kind=input_kind,  # type: ignore[arg-type]
        topology=topology,  # type: ignore[arg-type]
        target_label=target_label,
        target_bytes=target_bytes,
        iterations=1,
    )


@pytest.mark.parametrize(("target_label", "target_bytes"), TARGET_SIZES.items())
@pytest.mark.parametrize(
    ("message_kind", "topology"),
    [
        ("individual-task", "direct-shared"),
        ("individual-task", "task-type-routed"),
        ("batch-envelope", "direct-shared"),
        ("batch-envelope", "task-type-routed"),
        ("workflow", "workflow-stage"),
        ("status", "direct-shared"),
    ],
)
@pytest.mark.parametrize("input_kind", ["model", "canonical-mapping", "alias-mapping"])
def test_fixtures_match_exact_emitted_message_size(
    target_label: str,
    target_bytes: int,
    message_kind: str,
    topology: str,
    input_kind: str,
) -> None:
    fixture = build_fixture(_case(message_kind, input_kind, topology, target_label, target_bytes))

    assert fixture.actual_message_bytes == target_bytes
    assert fixture.case.target_label == target_label


def test_matrix_contains_every_supported_case_once() -> None:
    matrix = build_matrix({size: 1 for size in TARGET_SIZES.values()})
    identities = {(case.message_kind, case.input_kind, case.topology, case.target_bytes) for case in matrix}

    assert len(matrix) == 72
    assert len(identities) == 72
    assert {case.target_bytes for case in matrix} == set(TARGET_SIZES.values())
    assert {case.input_kind for case in matrix} == {
        "model",
        "canonical-mapping",
        "alias-mapping",
    }
    assert {case.message_kind for case in matrix} == {
        "individual-task",
        "batch-envelope",
        "workflow",
        "status",
    }
    assert {(case.message_kind, case.topology) for case in matrix} == {
        ("individual-task", "direct-shared"),
        ("individual-task", "task-type-routed"),
        ("batch-envelope", "direct-shared"),
        ("batch-envelope", "task-type-routed"),
        ("workflow", "workflow-stage"),
        ("status", "direct-shared"),
    }


@pytest.mark.asyncio
async def test_noop_exchange_counts_without_retaining_publish_history() -> None:
    exchange = NoOpExchange()
    first = Message(b"first")
    second = Message(b"second")

    await exchange.publish(first, routing_key="first.route")
    await exchange.publish(second, routing_key="second.route")

    assert exchange.published_count == 2
    assert exchange.active_publishes == 0
    assert exchange.peak_active_publishes == 1
    assert exchange.last_message is second
    assert exchange.last_routing_key == "second.route"
    assert not hasattr(exchange, "publish_calls")


def test_minimal_run_reports_complete_statistics_counts_and_preparation_probe() -> None:
    results = run_benchmarks(
        repeats=1,
        iterations_by_size={size: 1 for size in TARGET_SIZES.values()},
    )

    assert len(results) == 72
    assert all(result.actual_message_bytes in TARGET_SIZES.values() for result in results)
    assert all(result.median_ns_per_operation > 0 for result in results)
    assert all(result.median_absolute_deviation_ns == 0 for result in results)
    assert all(result.operations_per_second > 0 for result in results)
    assert all(result.throughput_mib_per_second > 0 for result in results)
    assert all(result.total_operations == 1 for result in results)
    assert all(result.total_published >= 1 for result in results)
    assert all(
        result.operations_per_second == pytest.approx(1_000_000_000 / result.median_ns_per_operation)
        for result in results
    )
    assert all(
        result.throughput_mib_per_second
        == pytest.approx(
            result.bytes_per_operation / (1024 * 1024) / (result.median_ns_per_operation / 1_000_000_000),
        )
        for result in results
    )
    assert all(
        result.total_published == result.total_operations * result.publications_per_operation for result in results
    )
    assert all(
        result.total_prepared == result.total_operations * result.preparations_per_operation for result in results
    )
    individual = [result for result in results if result.case.message_kind == "individual-task"]
    assert {result.preparations_per_operation for result in individual} == {2}
    assert {result.total_prepared for result in individual} == {2}


def test_legacy_mode_reproduces_duplicate_individual_preparation() -> None:
    results = run_benchmarks(
        repeats=1,
        iterations_by_size={size: 1 for size in TARGET_SIZES.values()},
        legacy_duplicate_preparation=True,
    )

    individual = [result for result in results if result.case.message_kind == "individual-task"]
    assert {result.preparations_per_operation for result in individual} == {4}


def test_html_round_trip_contains_methodology_counts_comparison_and_metadata(tmp_path: Path) -> None:
    case = _case("individual-task", "alias-mapping", "direct-shared", "1 KB", 1_024)
    baseline = BenchmarkResult(
        case=case,
        actual_message_bytes=1_024,
        bytes_per_operation=4_096,
        publications_per_operation=4,
        preparations_per_operation=8,
        repeats=3,
        sample_ns_per_operation=(20_000.0, 21_000.0, 22_000.0),
        median_ns_per_operation=21_000.0,
        median_absolute_deviation_ns=1_000.0,
        operations_per_second=47_619.0,
        throughput_mib_per_second=186.0,
        total_operations=21,
        total_prepared=168,
        total_published=84,
    )
    candidate = BenchmarkResult(
        **{
            **baseline.__dict__,
            "preparations_per_operation": 4,
            "median_ns_per_operation": 14_000.0,
            "relative_speedup": 1.5,
        }
    )
    environment = collect_environment(datetime(2026, 7, 29, 12, 0, tzinfo=UTC))
    baseline_path = tmp_path / "baseline.html"
    report_path = write_html_report(
        tmp_path / "nested" / "candidate.html",
        [candidate],
        environment,
        run_label="candidate <final>",
        baseline_path=baseline_path,
    )
    rendered = render_html(
        [candidate],
        environment,
        run_label="candidate <final>",
        baseline_path=baseline_path,
    )

    assert report_path.read_text(encoding="utf-8") == rendered
    if os.name == "posix":
        assert report_path.stat().st_mode & 0o777 == 0o644
    loaded = load_embedded_results(report_path)
    assert loaded[0].case == candidate.case
    assert loaded[0].median_ns_per_operation == candidate.median_ns_per_operation
    assert "<!doctype html>" in rendered
    assert "Methodology" in rendered
    assert "Before/after comparison" in rendered
    assert "Actionable bottleneck conclusions" in rendered
    assert "Environment and reproducibility" in rendered
    assert "Actual bytes/message" in rendered
    assert "Preparations/op" in rendered
    assert "Median µs/op" in rendered
    assert "MAD µs" in rendered
    assert "Operations/sec" in rendered
    assert "MiB/sec" in rendered
    assert "1.500×" in rendered
    assert "candidate &lt;final&gt;" in rendered
    assert "2026-07-29T12:00:00Z" in rendered


def test_cli_dispatches_publish_preparation_and_generates_html(tmp_path: Path, capsys) -> None:
    output_path = tmp_path / "publish-preparation.html"
    argv = ["run", "publish-preparation", "--repeats", "1"]
    for label in TARGET_SIZES:
        argv.extend(["--iterations", f"{label}=1"])
    argv.extend(["--run-label", "test", "--output", str(output_path)])

    assert main(argv) == 0
    assert output_path.is_file()
    assert "Completed publish-preparation: 72 measurements" in capsys.readouterr().out
    assert len(load_embedded_results(output_path)) == 72


def test_event_loop_is_reused_instead_of_asyncio_run_per_operation(monkeypatch) -> None:
    def forbidden_asyncio_run(_awaitable: object) -> None:
        raise AssertionError("asyncio.run() must not be used by publish-preparation samples")

    monkeypatch.setattr(asyncio, "run", forbidden_asyncio_run)

    results = run_benchmarks(
        repeats=1,
        iterations_by_size={size: 1 for size in TARGET_SIZES.values()},
    )

    assert len(results) == 72
