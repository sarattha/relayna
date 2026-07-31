from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from aio_pika import Message

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.cli import main  # noqa: E402
from benchmarks.publish_preparation import (  # noqa: E402
    BASELINE_SCHEMA_VERSION,
    DEFAULT_ITERATIONS,
    DEFAULT_REPEATS,
    TARGET_SIZES,
    TASKS_PER_OPERATION,
    BenchmarkCase,
    BenchmarkResult,
    NoOpExchange,
    baseline_metadata_path,
    build_fixture,
    build_matrix,
    load_baseline_results,
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


def _synthetic_baseline_result(case: BenchmarkCase) -> BenchmarkResult:
    publications = TASKS_PER_OPERATION if case.message_kind == "individual-task" else 1
    preparations = TASKS_PER_OPERATION * 2 if case.message_kind == "individual-task" else 1
    samples = tuple(float(10_000 + offset) for offset in range(DEFAULT_REPEATS))
    total_operations = case.iterations * DEFAULT_REPEATS
    return BenchmarkResult(
        case=case,
        actual_message_bytes=case.target_bytes,
        bytes_per_operation=case.target_bytes * publications,
        publications_per_operation=publications,
        preparations_per_operation=preparations,
        repeats=DEFAULT_REPEATS,
        sample_ns_per_operation=samples,
        median_ns_per_operation=samples[DEFAULT_REPEATS // 2],
        median_absolute_deviation_ns=1.0,
        operations_per_second=100_000.0,
        throughput_mib_per_second=100.0,
        total_operations=total_operations,
        total_prepared=total_operations * preparations,
        total_published=total_operations * publications,
    )


@pytest.fixture
def baseline_report(tmp_path: Path) -> Path:
    results = [_synthetic_baseline_result(case) for case in build_matrix()]
    report_path = write_html_report(
        tmp_path / "fixture" / "publish-preparation-baseline.html",
        results,
        {"Fixture": "synthetic baseline-loader coverage"},
        run_label="synthetic baseline",
    )
    metadata = {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "benchmark": "publish-preparation",
        "report": report_path.name,
        "report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
        "provenance": {
            "source_branch": "test-fixture",
            "base_revision": "test-base",
            "baseline_runtime_revision": "test-runtime",
            "artifact_revision": "test-artifact",
        },
        "methodology": {
            "fixed_clock_utc": "2025-01-01T00:00:00Z",
            "exact_message_sizes_bytes": TARGET_SIZES,
            "tasks_per_operation": TASKS_PER_OPERATION,
            "individual_preparations_per_operation": TASKS_PER_OPERATION * 2,
            "iterations_by_size": DEFAULT_ITERATIONS,
            "repeats": DEFAULT_REPEATS,
            "matrix_case_count": len(results),
        },
        "environment": {"fixture": "synthetic"},
    }
    baseline_metadata_path(report_path).write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report_path


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


def test_baseline_schema_matrix_and_preparation_evidence(baseline_report: Path) -> None:
    results = load_baseline_results(baseline_report)

    assert len(results) == 72
    assert {result.case for result in results} == set(build_matrix())
    individual = [result for result in results if result.case.message_kind == "individual-task"]
    assert {result.preparations_per_operation for result in individual} == {4}
    assert {result.publications_per_operation for result in individual} == {2}
    assert all(result.actual_message_bytes == result.case.target_bytes for result in results)


def _copy_baseline(tmp_path: Path, baseline_report: Path) -> Path:
    report_path = tmp_path / baseline_report.name
    report_path.write_bytes(baseline_report.read_bytes())
    metadata_path = baseline_metadata_path(report_path)
    metadata_path.write_bytes(baseline_metadata_path(baseline_report).read_bytes())
    return report_path


def test_baseline_loader_rejects_missing_report(tmp_path: Path) -> None:
    missing_report = tmp_path / "missing-baseline.html"
    with pytest.raises(FileNotFoundError, match="baseline report not found"):
        load_baseline_results(missing_report)


def test_baseline_loader_rejects_missing_metadata(tmp_path: Path, baseline_report: Path) -> None:
    report_path = tmp_path / baseline_report.name
    report_path.write_bytes(baseline_report.read_bytes())
    with pytest.raises(FileNotFoundError, match="baseline metadata not found"):
        load_baseline_results(report_path)


@pytest.mark.parametrize(
    ("mutation", "error_match"),
    [
        ({"schema_version": 999}, "Incompatible baseline schema"),
        ({"benchmark": "other-benchmark"}, "benchmark identity"),
        ({"report_sha256": "0" * 64}, "hash mismatch"),
    ],
)
def test_baseline_loader_rejects_incompatible_metadata(
    tmp_path: Path,
    baseline_report: Path,
    mutation: dict[str, object],
    error_match: str,
) -> None:
    report_path = _copy_baseline(tmp_path, baseline_report)
    metadata_path = baseline_metadata_path(report_path)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata.update(mutation)
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    with pytest.raises(ValueError, match=error_match):
        load_baseline_results(report_path)


def test_baseline_loader_rejects_matrix_and_requested_configuration_mismatch(
    tmp_path: Path,
    baseline_report: Path,
) -> None:
    report_path = _copy_baseline(tmp_path, baseline_report)
    metadata_path = baseline_metadata_path(report_path)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["methodology"]["matrix_case_count"] = 71
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    with pytest.raises(ValueError, match="baseline matrix"):
        load_baseline_results(report_path)

    baseline_results = load_baseline_results(baseline_report)
    with pytest.raises(ValueError, match="Baseline matrix is incompatible"):
        run_benchmarks(
            repeats=1,
            iterations_by_size={size: 1 for size in TARGET_SIZES.values()},
            baseline_results=baseline_results,
        )


def test_baseline_sidecar_hash_matches_html(baseline_report: Path) -> None:
    metadata = json.loads(baseline_metadata_path(baseline_report).read_text(encoding="utf-8"))

    assert metadata["report_sha256"] == hashlib.sha256(baseline_report.read_bytes()).hexdigest()


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
