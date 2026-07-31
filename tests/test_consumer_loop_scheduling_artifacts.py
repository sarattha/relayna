from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_RUN_ROOT = _ROOT / "reports" / "optimize-consumer-loop-scheduling" / "20260731T085401Z-1459da95-stable-paired"


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
def test_stabilized_runs_are_complete_unique_and_checksummed(side: str) -> None:
    run_dir = _RUN_ROOT / side
    manifest = _manifest(side)

    assert manifest["task"] == "optimize-consumer-loop-scheduling"
    assert manifest["source"]["clean_before_run"] is True
    assert manifest["execution"]["benchmark_harness_commit"] == "a9e8c305869bac89561a508889778480c05c0336"
    assert manifest["validation"] == {
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
    for line in (run_dir / "checksums.sha256").read_text(encoding="utf-8").splitlines():
        expected, relative = line.split("  ", maxsplit=1)
        assert hashlib.sha256((run_dir / relative).read_bytes()).hexdigest() == expected
    assert len(list(run_dir.glob("*/*.html"))) == 15

    for mode in ("disabled", "enabled-unsampled", "enabled-sampled-exported"):
        raw = json.loads((run_dir / mode / "consumer-processing.raw.json").read_text(encoding="utf-8"))["data"]
        assert {row["repeats"] for row in raw["per_message_results"]} == {5}
        assert {row["repeats"] for row in raw["consumer_loop_results"]} == {5}
        assert {row["message_count"] for row in raw["consumer_loop_results"]} == {64, 256, 2_048, 8_192}
        assert all(row["handler_count"] == row["ack_count"] for row in raw["consumer_loop_results"])
        assert all(row["reject_count"] == 0 for row in raw["consumer_loop_results"])
        assert all(row["peak_concurrency"] == row["prefetch"] for row in raw["consumer_loop_results"])


def test_stabilized_comparison_is_complete_and_inconclusive() -> None:
    data = json.loads((_RUN_ROOT / "comparison" / "comparison.json").read_text(encoding="utf-8"))

    assert data["validation"] == {
        "all_expected_cases_present_once_per_side": True,
        "baseline_case_count": 1224,
        "candidate_case_count": 1224,
        "comparison_case_count": 1224,
        "expected_case_count": 1224,
        "measurements_hand_edited": False,
        "unique_comparison_case_count": 1224,
    }
    assert len({cell["id"] for cell in data["cells"]}) == 1224
    assert data["assessment"]["verdict"] == "inconclusive"
    assert data["assessment"]["maximum_absolute_control_drift_percent"] == pytest.approx(5.3890436177)
    assert data["assessment"]["maximum_target_breakdown_regression_percent"] == pytest.approx(3.4615896688)
    assert data["assessment"]["target_breakdown_regressions_beyond_control_drift"] == 0
    targets = {summary["tracing_mode"]: summary["latency_percent"] for summary in data["target_summaries"]}
    assert targets == pytest.approx(
        {
            "disabled": 0.3855557767,
            "enabled-unsampled": -1.3289849484,
            "enabled-sampled-exported": -1.8645247169,
        }
    )
    assert data["export_validation"]["baseline_sampled_span_count"] == 811_880
    assert data["export_validation"]["candidate_sampled_span_count"] == 811_880
    assert data["export_validation"]["identical_names_kinds_statuses"] is True


def test_assessment_rejects_material_target_subgroup_regression() -> None:
    comparison_script = _load_script("compare_consumer_loop_scheduling")
    targets = [
        {"latency_percent": -8.0},
        {"latency_percent": -9.0},
        {"latency_percent": -10.0},
    ]
    controls = [{"latency_percent": 2.0}, {"latency_percent": -1.5}]
    breakdown = [
        {"latency_percent": -12.0},
        {"latency_percent": 3.0},
    ]

    assessment = comparison_script._assessment(targets, breakdown, controls)

    assert assessment["verdict"] == "not worth merging"
    assert assessment["target_breakdown_regressions_beyond_control_drift"] == 1
    assert "profile/prefetch subgroup" in assessment["rationale"]


def test_scheduling_comparison_generator_is_deterministic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    comparison_script = _load_script("compare_consumer_loop_scheduling")
    output = tmp_path / "comparison"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "compare_consumer_loop_scheduling.py",
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
