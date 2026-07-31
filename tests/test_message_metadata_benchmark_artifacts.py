from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


def _load_script(name: str) -> ModuleType:
    path = Path(__file__).parents[1] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _paired_manifests() -> tuple[dict[str, Any], dict[str, Any]]:
    baseline = {
        "run_id": "paired-run",
        "environment": {
            "python": "CPython 3.13.2",
            "uv": "0.11.26",
            "os": "test-os",
            "kernel": "test-kernel",
            "architecture": "arm64",
            "cpu": "test-cpu",
        },
        "execution": {
            "canonical_command": "uv run --extra benchmark python -m benchmarks run-all",
            "environment_controls": {
                "PYTHONHASHSEED": "0",
                "LC_ALL": "C",
                "LANG": "C",
                "TZ": "UTC",
            },
            "repetitions": "canonical",
            "warmups": "canonical",
            "started_at_utc": "2026-07-31T00:00:00Z",
            "finished_at_utc": "2026-07-31T00:01:00Z",
        },
        "packages": {
            "relayna": "1.4.30",
            "aio-pika": "9.6.1",
            "pydantic": "2.12.5",
        },
    }
    candidate = copy.deepcopy(baseline)
    candidate["execution"]["started_at_utc"] = baseline["execution"]["finished_at_utc"]
    candidate["execution"]["finished_at_utc"] = "2026-07-31T00:02:00Z"
    candidate["packages"]["relayna"] = "1.4.31"
    return baseline, candidate


def test_comparison_assessment_handles_improvement_regression_and_noise() -> None:
    comparison = _load_script("compare_message_metadata")

    improvement = comparison._assessment(
        per_message_delta_percent=-4.05,
        loop_delta_percent=-4.19,
        max_control_drift_percent=2.03,
    )
    regression = comparison._assessment(
        per_message_delta_percent=5.0,
        loop_delta_percent=4.0,
        max_control_drift_percent=2.0,
    )
    inconclusive = comparison._assessment(
        per_message_delta_percent=-1.0,
        loop_delta_percent=3.0,
        max_control_drift_percent=2.0,
    )

    assert improvement["outcome"] == "meaningful-improvement"
    assert improvement["improvement"] is True
    assert regression["outcome"] == "meaningful-regression"
    assert regression["improvement"] is False
    assert inconclusive["outcome"] == "inconclusive"
    assert inconclusive["meaningful"] is False


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("environment.python", "CPython 3.14.0", "environments differ"),
        ("execution.environment_controls", {"PYTHONHASHSEED": "1"}, "execution field differs"),
        ("packages.pydantic", "3.0.0", "third-party packages differ"),
        ("execution.started_at_utc", "2026-07-31T00:01:01Z", "back-to-back"),
    ],
)
def test_comparison_rejects_mismatched_pairs(
    field: str,
    value: Any,
    match: str,
) -> None:
    comparison = _load_script("compare_message_metadata")
    baseline, candidate = _paired_manifests()
    comparison._validate_pair(baseline, candidate)
    section, key = field.split(".", maxsplit=1)
    candidate[section][key] = value

    with pytest.raises(ValueError, match=match):
        comparison._validate_pair(baseline, candidate)


def test_retention_host_detection_is_portable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    retention = _load_script("retain_benchmark_run")
    monkeypatch.setattr(retention.platform, "system", lambda: "Linux")
    monkeypatch.setattr(retention.platform, "platform", lambda: "Linux-test")
    monkeypatch.setattr(retention.platform, "processor", lambda: "portable-cpu")
    monkeypatch.setattr(retention.platform, "machine", lambda: "x86_64")

    def unexpected_command(*command: str) -> str:
        raise AssertionError(f"unexpected macOS command on Linux: {command}")

    monkeypatch.setattr(retention, "_optional_command", unexpected_command)

    assert retention._operating_system() == "Linux-test"
    assert retention._cpu_name()
