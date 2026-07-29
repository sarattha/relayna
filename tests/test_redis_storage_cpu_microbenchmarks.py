from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.cli import main  # noqa: E402
from benchmarks.redis_storage_cpu import (  # noqa: E402
    REPRESENTATIONS,
    TARGET_SIZES,
    BenchmarkCase,
    BenchmarkResult,
    build_fixture,
    build_matrix,
    render_html,
    run_benchmarks,
    write_html_report,
)
from benchmarks.reporting import collect_environment  # noqa: E402


@pytest.mark.parametrize("representation", [representation.name for representation in REPRESENTATIONS])
@pytest.mark.parametrize("profile", ["ascii", "unicode-numeric"])
@pytest.mark.parametrize(("target_label", "target_bytes"), TARGET_SIZES.items())
def test_fixtures_match_exact_processed_size(
    representation: str,
    profile: str,
    target_label: str,
    target_bytes: int,
) -> None:
    fixture = build_fixture(representation, profile, target_bytes)  # type: ignore[arg-type]

    assert len(fixture.serialized) == target_bytes, target_label
    assert fixture.target_bytes == target_bytes


def test_profiles_produce_distinct_equal_size_fixtures() -> None:
    for representation in REPRESENTATIONS:
        ascii_fixture = build_fixture(representation.name, "ascii", 1_024)
        unicode_fixture = build_fixture(representation.name, "unicode-numeric", 1_024)

        assert ascii_fixture.serialized != unicode_fixture.serialized
        assert len(ascii_fixture.serialized) == len(unicode_fixture.serialized) == 1_024


def test_matrix_contains_every_requested_case_exactly_once() -> None:
    matrix = build_matrix({size: 1 for size in TARGET_SIZES.values()})
    identities = {
        (
            case.representation,
            case.profile,
            case.target_bytes,
            case.operation,
        )
        for case in matrix
    }

    assert len(matrix) == 72
    assert len(identities) == 72
    assert {case.target_bytes for case in matrix} == set(TARGET_SIZES.values())
    assert {case.profile for case in matrix} == {"ascii", "unicode-numeric"}
    assert {case.representation for case in matrix} == {representation.name for representation in REPRESENTATIONS}
    assert sum(case.operation == "encode" for case in matrix) == 30
    assert sum(case.operation == "decode" for case in matrix) == 30
    assert sum(case.operation == "canonical-hash" for case in matrix) == 12
    assert sum(case.is_baseline for case in matrix) == 36


def test_canonical_hash_fixtures_are_deterministic() -> None:
    for representation in (representation for representation in REPRESENTATIONS if representation.canonical_hash):
        first = build_fixture(representation.name, "unicode-numeric", 16_384)
        second = build_fixture(representation.name, "unicode-numeric", 16_384)
        ascii_fixture = build_fixture(representation.name, "ascii", 16_384)

        assert representation.canonical_hash is not None
        first_hash = representation.canonical_hash(first.value)
        assert first_hash == representation.canonical_hash(second.value)
        assert first_hash != representation.canonical_hash(ascii_fixture.value)
        assert len(first_hash) == 64


def test_minimal_run_reports_complete_nonzero_results() -> None:
    results = run_benchmarks(
        repeats=1,
        iterations_by_size={size: 1 for size in TARGET_SIZES.values()},
    )

    assert len(results) == 72
    assert all(result.actual_bytes in TARGET_SIZES.values() for result in results)
    assert all(result.median_ns_per_op > 0 for result in results)
    assert all(result.throughput_mb_s > 0 for result in results)
    assert all(result.relative_to_ascii > 0 for result in results)
    assert all(result.relative_to_ascii == 1.0 for result in results if result.case.is_baseline)


def test_html_report_contains_methodology_results_and_metadata(tmp_path: Path) -> None:
    case = BenchmarkCase(
        representation="status-json",
        representation_label="Generic status <JSON>",
        family="Generic JSON storage",
        profile="ascii",
        target_label="1 KB",
        target_bytes=1_024,
        operation="encode",
        iterations=7,
    )
    result = BenchmarkResult(
        case=case,
        actual_bytes=1_024,
        repeats=3,
        sample_ns_per_op=(2_000.0, 2_100.0, 2_200.0),
        median_ns_per_op=2_100.0,
        throughput_mb_s=487.619,
        relative_to_ascii=1.0,
    )
    environment = collect_environment(datetime(2026, 7, 29, 1, 0, tzinfo=UTC))

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
    assert "vs ASCII" in rendered
    assert "Redis process" in rendered
    assert "1,024" in rendered
    assert "7 × 3" in rendered
    assert "2026-07-29T01:00:00Z" in rendered
    assert "Generic status &lt;JSON&gt;" in rendered
    assert "<script" not in rendered


def test_cli_runs_redis_benchmark_with_specific_options(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    output_path = tmp_path / "redis-report.html"

    exit_code = main(
        [
            "run",
            "redis-storage-cpu",
            "--repeats",
            "1",
            "--iterations",
            "1 KB=1",
            "--iterations",
            "16 KB=1",
            "--iterations",
            "128 KB=1",
            "--output",
            str(output_path),
        ]
    )

    assert exit_code == 0
    assert output_path.is_file()
    assert "Completed redis-storage-cpu: 72 measurements" in capsys.readouterr().out
