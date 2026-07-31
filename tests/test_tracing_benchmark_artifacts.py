from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_RUN_ROOT = _ROOT / "reports" / "reduce-tracing-overhead" / "20260731T072226Z-283782ec"
_requires_retained_run = pytest.mark.skipif(
    not _RUN_ROOT.is_dir(),
    reason="retained benchmark reports are local-only artifacts",
)


def _load_script(name: str) -> ModuleType:
    path = _ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _consumer_rows(mode: str, *, candidate: bool) -> dict[str, list[dict[str, Any]]]:
    latency_ns = 8_000.0 if candidate and mode != "disabled" else 10_000.0
    per_message = [
        {
            "profile": f"profile-{index}",
            "input_kind": "canonical",
            "target_label": f"target-{index}",
            "actual_message_bytes": 1_024 + index,
            "iterations": 1,
            "repeats": 3,
            "total_messages": 3,
            "sample_ns_per_message": [latency_ns] * 3,
            "median_ns_per_message": latency_ns,
            "median_absolute_deviation_ns": 0.0,
            "messages_per_second": 1_000_000_000 / latency_ns,
            "throughput_mib_per_second": 1.0,
        }
        for index in range(16)
    ]
    consumer_loop = [
        {
            "profile": f"loop-profile-{index}",
            "input_kind": "canonical",
            "target_label": f"loop-target-{index}",
            "prefetch": index + 1,
            "actual_message_bytes": 2_048 + index,
            "message_count": 1,
            "repeats": 3,
            "total_bytes_per_sample": 2_048 + index,
            "sample_duration_ns": [latency_ns] * 3,
            "median_ns_per_message": latency_ns,
            "messages_per_second": 1_000_000_000 / latency_ns,
            "throughput_mib_per_second": 1.0,
        }
        for index in range(24)
    ]
    return {
        "per_message_results": per_message,
        "consumer_loop_results": consumer_loop,
    }


def _publish_rows(mode: str, *, candidate: bool) -> list[dict[str, Any]]:
    latency_ns = 8_000.0 if candidate and mode != "disabled" else 10_000.0
    return [
        {
            "message_kind": f"message-{index}",
            "input_kind": "model",
            "topology": "direct-shared",
            "target_label": f"target-{index}",
            "actual_message_bytes": 1_024 + index,
            "bytes_per_operation": 1_024 + index,
            "iterations": 1,
            "preparations_per_operation": 1,
            "publications_per_operation": 1,
            "repeats": 3,
            "total_operations": 3,
            "total_prepared": 3,
            "total_published": 3,
            "sample_ns_per_operation": [latency_ns] * 3,
            "median_ns_per_operation": latency_ns,
            "median_absolute_deviation_ns": 0.0,
            "operations_per_second": 1_000_000_000 / latency_ns,
            "throughput_mib_per_second": 1.0,
        }
        for index in range(72)
    ]


def _control_rows(count: int) -> list[dict[str, str]]:
    return [
        {
            "Case": str(index),
            "Direction": "control",
            "Actual bytes": str(1_024 + index),
            "Iterations × repeats": "1 × 3",
            "Median µs/op": "10.0",
            "Operations/s": "100000",
            "MiB/s": "1.0",
        }
        for index in range(count)
    ]


def _synthetic_manifest(*, candidate: bool) -> dict[str, Any]:
    return {
        "source": {
            "runtime_base_commit": "runtime-base",
            "runtime_content_sha256": {
                "src/relayna/observability/tracing.py": "candidate-target" if candidate else "baseline-target",
                "src/relayna/rabbitmq/client.py": "shared-client",
            },
            "commit": "candidate-commit" if candidate else "baseline-commit",
        },
        "packages": {"relayna": "1.4.32", "opentelemetry-sdk": "1.41.1"},
        "execution": {"environment_controls": {"PYTHONHASHSEED": "0"}},
        "tracing": {"modes": ["disabled", "enabled-unsampled", "enabled-sampled-exported"]},
        "environment": {
            "python": "CPython 3.13",
            "os": "test",
            "kernel": "test",
            "architecture": "test",
            "cpu": "test",
        },
        "dependency_state": {"lock_sha256": {"uv.lock": "shared-lock"}},
        "validation": {
            "observed_total_measurements": 1_224,
            "unique_qualified_case_count": 1_224,
            "all_expected_cases_present_once": True,
        },
    }


def _write_synthetic_run(run_dir: Path, comparison_script: ModuleType, *, candidate: bool) -> None:
    _write_json(run_dir / "manifest.json", _synthetic_manifest(candidate=candidate))
    for mode in comparison_script.TRACING_MODES:
        for benchmark in comparison_script.BENCHMARKS:
            payload: dict[str, Any] = {
                "schema_version": 1,
                "benchmark": benchmark,
                "tracing_mode": mode,
            }
            if benchmark == "consumer-processing":
                payload["data"] = _consumer_rows(mode, candidate=candidate)
            elif benchmark == "publish-preparation":
                payload["data"] = {"results": _publish_rows(mode, candidate=candidate)}
            else:
                payload["rows"] = _control_rows(comparison_script.EXPECTED_PER_MODE[benchmark])
            _write_json(run_dir / mode / comparison_script.RAW_FILES[benchmark], payload)
    _write_json(
        run_dir / "enabled-sampled-exported" / "tracing-suite.json",
        {
            "exported_spans": {
                "count": 1,
                "kinds": {"INTERNAL": 1},
                "names": {"fixture": 1},
                "statuses": {"UNSET": 1},
            }
        },
    )


def _manifest(side: str) -> dict[str, object]:
    return json.loads((_RUN_ROOT / side / "manifest.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("side", ["baseline", "candidate"])
@_requires_retained_run
def test_retained_runs_have_complete_unique_three_mode_suites_and_valid_checksums(side: str) -> None:
    run_dir = _RUN_ROOT / side
    manifest = _manifest(side)
    validation = manifest["validation"]
    assert validation == {
        "all_expected_cases_present_once": True,
        "expected_benchmarks": [
            "consumer-processing",
            "envelope-serialization",
            "json-engine-evaluation",
            "publish-preparation",
            "redis-storage-cpu",
        ],
        "expected_total_measurements": 1224,
        "expected_tracing_modes": [
            "disabled",
            "enabled-unsampled",
            "enabled-sampled-exported",
        ],
        "observed_total_measurements": 1224,
        "raw_measurements_hand_edited": False,
        "report_count": 15,
        "standalone_html_validated": True,
        "unique_qualified_case_count": 1224,
    }
    checksum_lines = (run_dir / "checksums.sha256").read_text(encoding="utf-8").splitlines()
    assert len(checksum_lines) == 35
    for line in checksum_lines:
        expected, relative = line.split("  ", maxsplit=1)
        assert hashlib.sha256((run_dir / relative).read_bytes()).hexdigest() == expected
    for report in run_dir.glob("*/*.html"):
        content = report.read_text(encoding="utf-8")
        assert content.lower().startswith("<!doctype html>")
        assert "</html>" in content.lower()
        assert "Tracing mode" in content


@_requires_retained_run
def test_sampled_export_inventory_is_identical_and_unsampled_stays_unexported() -> None:
    baseline = json.loads(
        (_RUN_ROOT / "baseline" / "enabled-sampled-exported" / "tracing-suite.json").read_text(encoding="utf-8")
    )
    candidate = json.loads(
        (_RUN_ROOT / "candidate" / "enabled-sampled-exported" / "tracing-suite.json").read_text(encoding="utf-8")
    )
    assert baseline["exported_spans"] == candidate["exported_spans"]
    assert baseline["exported_spans"] == {
        "count": 504104,
        "kinds": {"CONSUMER": 48728, "PRODUCER": 455376},
        "names": {
            "relayna.consumer.task_message": 48728,
            "relayna.rabbitmq.publish_batch": 113844,
            "relayna.rabbitmq.publish_status": 56922,
            "relayna.rabbitmq.publish_task": 227688,
            "relayna.rabbitmq.publish_workflow": 56922,
        },
        "statuses": {"UNSET": 504104},
    }
    for side in ("baseline", "candidate"):
        unsampled = json.loads(
            (_RUN_ROOT / side / "enabled-unsampled" / "tracing-suite.json").read_text(encoding="utf-8")
        )
        assert unsampled["exported_spans"]["count"] == 0


@_requires_retained_run
def test_comparison_contains_every_case_and_supported_performance_claim() -> None:
    comparison = json.loads((_RUN_ROOT / "comparison" / "comparison.json").read_text(encoding="utf-8"))
    assert comparison["validation"] == {
        "all_expected_cases_present_once_per_side": True,
        "baseline_case_count": 1224,
        "candidate_case_count": 1224,
        "comparison_case_count": 1224,
        "expected_case_count": 1224,
        "measurements_hand_edited": False,
        "unique_comparison_case_count": 1224,
    }
    assert len({cell["id"] for cell in comparison["cells"]}) == 1224
    assert comparison["assessment"]["verdict"] == "worth merging"
    assert comparison["assessment"]["maximum_absolute_control_drift_percent"] == pytest.approx(1.5308517)
    summaries = {
        (item["tracing_mode"], item["benchmark"]): item["latency_percent"] for item in comparison["benchmark_summaries"]
    }
    assert summaries[("enabled-unsampled", "consumer-processing")] == pytest.approx(-16.4739312)
    assert summaries[("enabled-sampled-exported", "consumer-processing")] == pytest.approx(-16.2912763)
    assert summaries[("enabled-unsampled", "publish-preparation")] == pytest.approx(-13.8637034)
    assert summaries[("enabled-sampled-exported", "publish-preparation")] == pytest.approx(-12.1411611)


def test_comparison_generator_is_deterministic_and_rejects_package_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    comparison_script = _load_script("compare_tracing_benchmarks")
    baseline_dir = tmp_path / "baseline"
    candidate_dir = tmp_path / "candidate"
    _write_synthetic_run(baseline_dir, comparison_script, candidate=False)
    _write_synthetic_run(candidate_dir, comparison_script, candidate=True)

    outputs = (tmp_path / "comparison-one", tmp_path / "comparison-two")
    for output in outputs:
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "compare_tracing_benchmarks.py",
                "--baseline-dir",
                str(baseline_dir),
                "--candidate-dir",
                str(candidate_dir),
                "--output-dir",
                str(output),
            ],
        )
        assert comparison_script.main() == 0
    for name in ("comparison.html", "comparison.json", "manifest.json", "checksums.sha256"):
        assert (outputs[0] / name).read_bytes() == (outputs[1] / name).read_bytes()

    baseline = _synthetic_manifest(candidate=False)
    candidate = _synthetic_manifest(candidate=True)
    candidate["packages"]["opentelemetry-sdk"] = "0.0.0"
    with pytest.raises(ValueError, match="Resolved benchmark packages differ"):
        comparison_script._validate_pair(baseline, candidate)

    candidate = _synthetic_manifest(candidate=True)
    candidate["source"]["runtime_content_sha256"]["src/relayna/rabbitmq/client.py"] = "0" * 64
    with pytest.raises(ValueError, match="Non-target runtime content differs.*rabbitmq/client.py"):
        comparison_script._validate_pair(baseline, candidate)
