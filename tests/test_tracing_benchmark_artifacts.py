from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_RUN_ROOT = _ROOT / "reports" / "reduce-tracing-overhead" / "20260731T072226Z-283782ec"


def _load_script(name: str) -> ModuleType:
    path = _ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _manifest(side: str) -> dict[str, object]:
    return json.loads((_RUN_ROOT / side / "manifest.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("side", ["baseline", "candidate"])
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
    output = tmp_path / "comparison"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "compare_tracing_benchmarks.py",
            "--baseline-dir",
            str(_RUN_ROOT / "baseline"),
            "--candidate-dir",
            str(_RUN_ROOT / "candidate"),
            "--output-dir",
            str(output),
        ],
    )

    assert comparison_script.main() == 0
    for name in ("comparison.html", "comparison.json", "manifest.json", "checksums.sha256"):
        assert (output / name).read_bytes() == (_RUN_ROOT / "comparison" / name).read_bytes()

    baseline = _manifest("baseline")
    candidate = json.loads(json.dumps(_manifest("candidate")))
    candidate["packages"]["opentelemetry-sdk"] = "0.0.0"
    with pytest.raises(ValueError, match="Resolved benchmark packages differ"):
        comparison_script._validate_pair(baseline, candidate)

    candidate = json.loads(json.dumps(_manifest("candidate")))
    candidate["source"]["runtime_content_sha256"]["src/relayna/rabbitmq/client.py"] = "0" * 64
    with pytest.raises(ValueError, match="Non-target runtime content differs.*rabbitmq/client.py"):
        comparison_script._validate_pair(baseline, candidate)
