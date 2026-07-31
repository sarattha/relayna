from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.reporting import collect_environment  # noqa: E402
from benchmarks.tracing_suite import TRACING_MODES, _configure_tracing  # noqa: E402


def test_reporting_includes_tracing_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REL_BENCHMARK_TRACING_MODE", "enabled-unsampled")
    monkeypatch.setenv("REL_BENCHMARK_SAMPLER", "StaticSampler")

    environment = collect_environment()

    assert environment["Tracing mode"] == "enabled-unsampled"
    assert environment["OpenTelemetry sampler"] == "StaticSampler"


@pytest.mark.parametrize("mode", TRACING_MODES)
def test_each_tracing_mode_runs_in_an_isolated_process(mode: str, tmp_path: Path) -> None:
    script = """
import json
from opentelemetry import trace
from benchmarks.tracing_suite import _configure_tracing
provider, summary = _configure_tracing(MODE)
tracer = trace.get_tracer("test")
with tracer.start_as_current_span("test.span") as span:
    sampled = span.get_span_context().trace_flags.sampled
if provider is not None:
    provider.shutdown()
print(json.dumps({"count": summary.count, "sampled": sampled}))
""".replace("MODE", repr(mode))
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
    )

    result = json.loads(completed.stdout)
    assert result["sampled"] is (mode == "enabled-sampled-exported")
    assert result["count"] == (1 if mode == "enabled-sampled-exported" else 0)


def test_configure_disabled_mode_uses_api_noop_provider() -> None:
    provider, summary = _configure_tracing("disabled")

    assert provider is None
    assert summary.count == 0
    assert os.environ["REL_BENCHMARK_TRACING_MODE"] == "disabled"
