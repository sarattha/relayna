from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.envelope_serialization import (  # noqa: E402
    TARGET_SIZES,
    BenchmarkCase,
    BenchmarkResult,
    build_fixture,
    build_matrix,
    collect_environment,
    current_outbound,
    render_html,
    run_benchmarks,
    write_html_report,
)


@pytest.mark.parametrize("envelope_kind", ["task", "batch"])
@pytest.mark.parametrize(("target_label", "target_bytes"), TARGET_SIZES.items())
def test_fixtures_match_exact_current_wire_size(envelope_kind: str, target_label: str, target_bytes: int) -> None:
    fixture = build_fixture(envelope_kind, target_bytes)  # type: ignore[arg-type]

    assert len(current_outbound(fixture)) == target_bytes, target_label


def test_matrix_contains_every_case_exactly_once() -> None:
    matrix = build_matrix({size: 1 for size in TARGET_SIZES.values()})
    identities = {
        (
            case.envelope_kind,
            case.target_bytes,
            case.direction,
            case.implementation,
        )
        for case in matrix
    }

    assert len(matrix) == 32
    assert len(identities) == 32
    assert {case.target_bytes for case in matrix} == set(TARGET_SIZES.values())
    assert {case.envelope_kind for case in matrix} == {"task", "batch"}
    assert {case.direction for case in matrix} == {"outbound", "inbound"}
    assert {case.implementation for case in matrix} == {"current", "pydantic-direct"}
    assert sum(case.is_baseline for case in matrix) == 16


def test_minimal_run_reports_complete_nonzero_results() -> None:
    results = run_benchmarks(
        repeats=1,
        iterations_by_size={size: 1 for size in TARGET_SIZES.values()},
    )

    assert len(results) == 32
    assert all(result.actual_bytes > 0 for result in results)
    assert all(result.median_ns_per_op > 0 for result in results)
    assert all(result.throughput_mb_s > 0 for result in results)
    assert all(result.relative_to_current > 0 for result in results)
    assert all(result.relative_to_current == 1.0 for result in results if result.case.is_baseline)


def test_html_report_contains_methodology_results_and_metadata(tmp_path) -> None:
    case = BenchmarkCase(
        envelope_kind="task",
        target_label="1 KB",
        target_bytes=1_024,
        direction="outbound",
        implementation="current",
        implementation_label="Current <baseline>",
        is_baseline=True,
        iterations=7,
    )
    result = BenchmarkResult(
        case=case,
        actual_bytes=1_024,
        repeats=3,
        sample_ns_per_op=(2_000.0, 2_100.0, 2_200.0),
        median_ns_per_op=2_100.0,
        throughput_mb_s=487.619,
        relative_to_current=1.0,
    )
    environment = collect_environment(datetime(2026, 7, 28, 12, 0, tzinfo=UTC))

    rendered = render_html([result], environment)
    report_path = write_html_report(tmp_path / "nested" / "report.html", [result], environment)

    assert report_path == (tmp_path / "nested" / "report.html").resolve()
    assert report_path.read_text(encoding="utf-8") == rendered
    if os.name == "posix":
        assert report_path.stat().st_mode & 0o777 == 0o644
    assert "<!doctype html>" in rendered
    assert "Methodology" in rendered
    assert "Results" in rendered
    assert "Environment and reproducibility" in rendered
    assert "Actual bytes" in rendered
    assert "Iterations × repeats" in rendered
    assert "Median µs/op" in rendered
    assert "Throughput MB/s" in rendered
    assert "vs current" in rendered
    assert "1,024" in rendered
    assert "7 × 3" in rendered
    assert "2026-07-28T12:00:00Z" in rendered
    assert "Current &lt;baseline&gt;" in rendered
    assert "<script" not in rendered
