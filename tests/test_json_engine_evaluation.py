from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.json_engine_evaluation import (  # noqa: E402
    DEFAULT_PROFILES,
    TARGET_SIZES,
    BenchmarkResult,
    build_fixture,
    build_matrix,
    collect_environment,
    orjson_outbound,
    pydantic_core_outbound,
    pydantic_direct_inbound,
    pydantic_direct_outbound,
    render_html,
    require_orjson,
    run_benchmarks,
    run_compatibility_checks,
    stdlib_inbound,
    stdlib_outbound,
    write_html_report,
)
from relayna.contracts import BatchTaskEnvelope, TaskEnvelope  # noqa: E402


@pytest.fixture(scope="module")
def minimal_results() -> list[BenchmarkResult]:
    return run_benchmarks(
        repeats=1,
        iterations_by_size={size: 1 for size in TARGET_SIZES.values()},
        profiles=("ascii",),
    )


@pytest.fixture(scope="module")
def compatibility_findings():
    return run_compatibility_checks()


@pytest.mark.parametrize("envelope_kind", ["task", "batch"])
@pytest.mark.parametrize("profile", DEFAULT_PROFILES)
@pytest.mark.parametrize(("target_label", "target_bytes"), TARGET_SIZES.items())
def test_fixtures_match_exact_current_wire_size(
    envelope_kind: str,
    profile: str,
    target_label: str,
    target_bytes: int,
) -> None:
    fixture = build_fixture(envelope_kind, target_bytes, profile)  # type: ignore[arg-type]

    assert len(stdlib_outbound(fixture)) == target_bytes, target_label


def test_matrix_contains_every_complete_path_case_exactly_once() -> None:
    matrix = build_matrix({size: 1 for size in TARGET_SIZES.values()})
    identities = {
        (
            case.envelope_kind,
            case.profile,
            case.target_bytes,
            case.direction,
            case.inbound_shape,
            case.engine,
        )
        for case in matrix
    }

    assert len(matrix) == 192
    assert len(identities) == 192
    assert {case.envelope_kind for case in matrix} == {"task", "batch"}
    assert {case.profile for case in matrix} == {"ascii", "unicode-numeric"}
    assert {case.target_bytes for case in matrix} == set(TARGET_SIZES.values())
    assert {case.direction for case in matrix} == {"outbound", "inbound"}
    assert {case.inbound_shape for case in matrix if case.direction == "inbound"} == {
        "canonical",
        "alias-compatible",
    }
    assert {case.engine for case in matrix} == {
        "stdlib",
        "pydantic-core",
        "pydantic-direct",
        "orjson",
    }
    assert sum(case.is_baseline for case in matrix) == 48


def test_outbound_candidates_preserve_parsed_semantics_but_not_current_bytes() -> None:
    fixture = build_fixture("task", TARGET_SIZES["1 KB"], "unicode-numeric")
    baseline = stdlib_outbound(fixture)
    baseline_semantics = json.loads(baseline)
    candidates = [
        pydantic_core_outbound(fixture),
        pydantic_direct_outbound(fixture),
        orjson_outbound(fixture),
    ]

    assert all(json.loads(candidate) == baseline_semantics for candidate in candidates)
    assert all(candidate != baseline for candidate in candidates)


@pytest.mark.parametrize("envelope_kind", ["task", "batch"])
def test_canonical_and_alias_paths_validate_equivalent_models(envelope_kind: str) -> None:
    fixture = build_fixture(envelope_kind, TARGET_SIZES["1 KB"], "ascii")  # type: ignore[arg-type]
    canonical = stdlib_outbound(fixture)
    prepared = fixture.model_dump(mode="json", exclude_none=True)
    if envelope_kind == "task":
        prepared["documentId"] = prepared.pop("task_id")
    else:
        for task in prepared["tasks"]:
            task["documentId"] = task.pop("task_id")
    alias_payload = json.dumps(prepared).encode()

    current = stdlib_inbound(envelope_kind, canonical)  # type: ignore[arg-type]
    direct = pydantic_direct_inbound(envelope_kind, canonical, alias_compatible=False)  # type: ignore[arg-type]
    fallback = pydantic_direct_inbound(envelope_kind, alias_payload, alias_compatible=True)  # type: ignore[arg-type]

    expected = fixture.model_dump(mode="json", exclude_none=True)
    assert current.model_dump(mode="json", exclude_none=True) == expected
    assert direct.model_dump(mode="json", exclude_none=True) == expected
    assert fallback.model_dump(mode="json", exclude_none=True) == expected
    assert isinstance(fallback, TaskEnvelope if envelope_kind == "task" else BatchTaskEnvelope)


def test_minimal_run_reports_complete_positive_metrics(minimal_results) -> None:
    assert len(minimal_results) == 96
    assert all(result.actual_bytes > 0 for result in minimal_results)
    assert all(result.median_ns_per_op > 0 for result in minimal_results)
    assert all(result.p25_ns_per_op > 0 for result in minimal_results)
    assert all(result.p75_ns_per_op >= result.p25_ns_per_op for result in minimal_results)
    assert all(result.operations_per_second > 0 for result in minimal_results)
    assert all(result.throughput_mib_s > 0 for result in minimal_results)
    assert all(result.relative_to_current > 0 for result in minimal_results)
    assert all(result.relative_to_current == 1.0 for result in minimal_results if result.case.is_baseline)
    assert all(result.p25_ns_per_op == result.p75_ns_per_op for result in minimal_results)


def _finding(findings, scenario: str, engine: str):
    return next(item for item in findings if item.scenario == scenario and item.engine == engine)


def test_compatibility_findings_capture_required_semantic_boundaries(compatibility_findings) -> None:
    scenarios = {finding.scenario for finding in compatibility_findings}

    assert len(compatibility_findings) == 61
    assert {
        "Prepared Unicode/numeric TaskEnvelope",
        "Valid Unicode and UTF-8",
        "Invalid UTF-8 byte inside a JSON string",
        "Built-in documentId alias",
        "Configured jobId alias",
        "Inbound integer beyond 64-bit (2**100)",
        "Outbound integer beyond 64-bit (2**100)",
        "Inbound acceptance and errors",
        "Non-string mapping keys accepted by current stdlib",
        "None mapping key coercion",
        "Tuple mapping key coercion",
        "Malformed JSON",
        "Valid JSON with invalid envelope shape",
        "Non-finite JSON tokens",
        "Outbound NaN, Infinity, and -Infinity",
        "Datetime, UUID, and nested model after current model_dump(mode='json')",
        "Canonical hash/dedup or persisted byte inputs",
    } - scenarios == {"Inbound acceptance and errors"}

    current_invalid_utf8 = _finding(
        compatibility_findings,
        "Invalid UTF-8 byte inside a JSON string",
        "Released v1.4.29 stdlib reference",
    )
    core_invalid_utf8 = _finding(
        compatibility_findings,
        "Invalid UTF-8 byte inside a JSON string",
        "New production: Pydantic Core transport",
    )
    orjson_huge = _finding(
        compatibility_findings,
        "Inbound integer beyond 64-bit (2**100)",
        "orjson",
    )
    direct_alias = _finding(
        compatibility_findings,
        "Built-in documentId alias",
        "Direct Pydantic model JSON",
    )
    malformed = _finding(compatibility_findings, "Malformed JSON", "Direct Pydantic model JSON")
    invalid_shape = _finding(
        compatibility_findings,
        "Valid JSON with invalid envelope shape",
        "Direct Pydantic model JSON",
    )
    released_none_key = _finding(
        compatibility_findings,
        "None mapping key coercion",
        "Released v1.4.29 stdlib reference",
    )
    production_none_key = _finding(
        compatibility_findings,
        "None mapping key coercion",
        "New production: Pydantic Core transport",
    )
    released_tuple_key = _finding(
        compatibility_findings,
        "Tuple mapping key coercion",
        "Released v1.4.29 stdlib reference",
    )
    production_tuple_key = _finding(
        compatibility_findings,
        "Tuple mapping key coercion",
        "New production: Pydantic Core transport",
    )

    assert current_invalid_utf8.outcome == "accepted"
    assert core_invalid_utf8.outcome == "rejected"
    assert core_invalid_utf8.rejection_stage == "JSON parse"
    assert orjson_huge.outcome == "precision loss"
    assert direct_alias.outcome == "compatible with fallback"
    assert "fallback is required" in direct_alias.detail
    assert malformed.rejection_stage == "JSON parse"
    assert invalid_shape.rejection_stage == "envelope validation"
    assert '"null"' in released_none_key.detail
    assert '"None"' in production_none_key.detail
    assert released_tuple_key.outcome == "rejected"
    assert '"x,y"' in production_tuple_key.detail


def test_optional_orjson_handling_is_explicit_and_partial_matrix_remains_usable() -> None:
    with pytest.raises(RuntimeError, match="--extra benchmark"):
        require_orjson(None)

    partial_matrix = build_matrix(
        {size: 1 for size in TARGET_SIZES.values()},
        profiles=("ascii",),
        include_orjson=False,
    )
    partial_results = run_benchmarks(
        repeats=1,
        iterations_by_size={size: 1 for size in TARGET_SIZES.values()},
        profiles=("ascii",),
        include_orjson=False,
        orjson_module=None,
    )

    assert len(partial_matrix) == 72
    assert len(partial_results) == 72
    assert all(case.engine != "orjson" for case in partial_matrix)


def test_html_report_contains_decision_compatibility_packaging_and_next_benchmark(
    tmp_path,
    minimal_results,
    compatibility_findings,
) -> None:
    environment = collect_environment(datetime(2026, 7, 29, 12, 0, tzinfo=UTC))

    rendered = render_html(
        minimal_results,
        compatibility_findings,
        environment,
        include_orjson=True,
    )
    report_path = write_html_report(
        tmp_path / "nested" / "report.html",
        minimal_results,
        compatibility_findings,
        environment,
        include_orjson=True,
    )

    assert report_path == (tmp_path / "nested" / "report.html").resolve()
    assert report_path.read_text(encoding="utf-8") == rendered
    if os.name == "posix":
        assert report_path.stat().st_mode & 0o777 == 0o644
    assert "<!doctype html>" in rendered
    assert "Executive decision" in rendered
    assert "Performance" in rendered
    assert "Compatibility" in rendered
    assert "Packaging and reproducibility" in rendered
    assert "Implemented production strategy" in rendered
    assert "Next benchmark" in rendered
    assert "Median µs/op" in rendered
    assert "P25–P75 µs/op" in rendered
    assert "Operations/s" in rendered
    assert "MiB/s" in rendered
    assert "orjson==3.11.9" in rendered
    assert "Pydantic Core 2.41.5 (production)" in rendered
    assert "pydantic_core-2.41.5-cp314-cp314-manylinux_2_17_aarch64" in rendered
    assert "CPython 3.14" in rendered
    assert "Linux aarch64" in rendered
    assert "macOS x86_64" in rendered
    assert "Canonical hash/dedup or persisted byte inputs" in rendered
    assert "2026-07-29T12:00:00Z" in rendered
    assert "<script" not in rendered
