"""Shared metadata and artifact-writing helpers for Relayna benchmarks."""

from __future__ import annotations

import os
import platform
import sys
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

DEFAULT_METADATA_PACKAGES = ("relayna", "pydantic", "pydantic-core")
_TRACING_ENVIRONMENT_KEYS = {
    "REL_BENCHMARK_TRACING_MODE": "Tracing mode",
    "REL_BENCHMARK_TRACER_PROVIDER": "OpenTelemetry tracer provider",
    "REL_BENCHMARK_SAMPLER": "OpenTelemetry sampler",
    "REL_BENCHMARK_SPAN_PROCESSOR": "OpenTelemetry span processor",
    "REL_BENCHMARK_EXPORTER": "OpenTelemetry exporter",
    "REL_BENCHMARK_PROPAGATOR": "OpenTelemetry propagator",
}


def collect_environment(
    timestamp: datetime | None = None,
    *,
    package_names: Sequence[str] = DEFAULT_METADATA_PACKAGES,
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Collect common environment metadata plus benchmark-specific values."""

    captured_at = datetime.now(UTC) if timestamp is None else timestamp.astimezone(UTC)
    metadata = {
        "Timestamp (UTC)": captured_at.isoformat().replace("+00:00", "Z"),
        "Python": platform.python_version(),
        "Python implementation": platform.python_implementation(),
        "Python executable": Path(sys.executable).name,
        "Platform": platform.platform(),
        "Architecture": platform.machine() or "unknown",
        "Processor": platform.processor() or "unknown",
    }
    for package_name in package_names:
        try:
            metadata[f"Package: {package_name}"] = version(package_name)
        except PackageNotFoundError:
            metadata[f"Package: {package_name}"] = "not installed"
    for environment_key, metadata_key in _TRACING_ENVIRONMENT_KEYS.items():
        if value := os.environ.get(environment_key):
            metadata[metadata_key] = value
    metadata.update(extra or {})
    return metadata


def write_text_artifact(output_path: Path, content: str) -> Path:
    """Atomically write a UTF-8 text artifact with repository-readable mode."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(content)
            temporary_path = Path(temporary.name)
        temporary_path.chmod(0o644)
        temporary_path.replace(output_path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return output_path.resolve()
