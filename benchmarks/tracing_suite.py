"""Run every canonical benchmark under isolated OpenTelemetry configurations."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from benchmarks.cli import _default_arguments
from benchmarks.registry import registered_benchmarks
from benchmarks.reporting import write_text_artifact

TracingMode = Literal["disabled", "enabled-unsampled", "enabled-sampled-exported"]
TRACING_MODES: tuple[TracingMode, ...] = (
    "disabled",
    "enabled-unsampled",
    "enabled-sampled-exported",
)
SUMMARY_FILE = "tracing-suite.json"


@dataclass
class _ExporterSummary:
    count: int
    names: Counter[str]
    kinds: Counter[str]
    statuses: Counter[str]


def _configure_tracing(mode: TracingMode) -> tuple[Any | None, _ExporterSummary]:
    from opentelemetry import propagate, trace

    summary = _ExporterSummary(count=0, names=Counter(), kinds=Counter(), statuses=Counter())
    if mode == "disabled":
        provider = trace.get_tracer_provider()
        configuration = {
            "REL_BENCHMARK_TRACING_MODE": mode,
            "REL_BENCHMARK_TRACER_PROVIDER": type(provider).__name__,
            "REL_BENCHMARK_SAMPLER": "none; OpenTelemetry API no-op provider",
            "REL_BENCHMARK_SPAN_PROCESSOR": "none",
            "REL_BENCHMARK_EXPORTER": "none",
            "REL_BENCHMARK_PROPAGATOR": type(propagate.get_global_textmap()).__name__,
        }
        os.environ.update(configuration)
        return None, summary

    from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
    from opentelemetry.sdk.trace.sampling import ALWAYS_OFF, ALWAYS_ON

    class CountingExporter(SpanExporter):
        def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
            summary.count += len(spans)
            summary.names.update(span.name for span in spans)
            summary.kinds.update(span.kind.name for span in spans)
            summary.statuses.update(span.status.status_code.name for span in spans)
            return SpanExportResult.SUCCESS

    sampler = ALWAYS_OFF if mode == "enabled-unsampled" else ALWAYS_ON
    exporter = CountingExporter()
    provider = TracerProvider(sampler=sampler)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    configuration = {
        "REL_BENCHMARK_TRACING_MODE": mode,
        "REL_BENCHMARK_TRACER_PROVIDER": type(provider).__name__,
        "REL_BENCHMARK_SAMPLER": type(sampler).__name__,
        "REL_BENCHMARK_SPAN_PROCESSOR": "SimpleSpanProcessor",
        "REL_BENCHMARK_EXPORTER": "CountingExporter (synchronous, non-retaining)",
        "REL_BENCHMARK_PROPAGATOR": type(propagate.get_global_textmap()).__name__,
    }
    os.environ.update(configuration)
    return provider, summary


def _worker(mode: TracingMode, output_root: Path, *, quick: bool) -> int:
    provider, exporter = _configure_tracing(mode)
    reports: list[dict[str, Any]] = []
    output_root.mkdir(parents=True, exist_ok=False)
    try:
        for definition in registered_benchmarks():
            arguments = _default_arguments(definition)
            arguments.output = output_root / definition.default_output.name
            if quick:
                if hasattr(arguments, "repeats"):
                    arguments.repeats = 1
                if hasattr(arguments, "iterations"):
                    arguments.iterations = (
                        [f"{label}=1" for label in ("1 KB", "16 KB", "128 KB", "1 MB")]
                        if definition.name in {"consumer-processing", "publish-preparation"}
                        else [(size, 1) for size in (1_024, 16_384, 131_072, 1_048_576)]
                    )
                if hasattr(arguments, "loop_messages"):
                    arguments.loop_messages = [f"{label}=1" for label in ("1 KB", "16 KB", "128 KB", "1 MB")]
            before_count = exporter.count
            before_names = exporter.names.copy()
            outcome = definition.run(arguments)
            exported_names = exporter.names - before_names
            reports.append(
                {
                    "benchmark": definition.name,
                    "measurement_count": outcome.measurement_count,
                    "artifacts": [path.name for path in outcome.artifacts],
                    "exported_span_count": exporter.count - before_count,
                    "exported_span_names": dict(sorted(exported_names.items())),
                }
            )
    finally:
        if provider is not None:
            provider.shutdown()

    expected_export = mode == "enabled-sampled-exported"
    traced = {
        item["benchmark"]: item["exported_span_count"]
        for item in reports
        if item["benchmark"] in {"consumer-processing", "publish-preparation"}
    }
    if expected_export and (set(traced) != {"consumer-processing", "publish-preparation"} or min(traced.values()) < 1):
        raise RuntimeError(f"Sampled tracing did not export producer and consumer spans: {traced}")
    if not expected_export and any(item["exported_span_count"] for item in reports):
        raise RuntimeError(f"Non-exporting tracing mode unexpectedly exported spans: {reports}")

    summary = {
        "schema_version": 1,
        "tracing_mode": mode,
        "canonical": not quick,
        "configuration": {key: value for key, value in os.environ.items() if key.startswith("REL_BENCHMARK_")},
        "reports": reports,
        "exported_spans": {
            "count": exporter.count,
            "names": dict(sorted(exporter.names.items())),
            "kinds": dict(sorted(exporter.kinds.items())),
            "statuses": dict(sorted(exporter.statuses.items())),
        },
    }
    write_text_artifact(output_root / SUMMARY_FILE, json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return 0


def _parent(output_root: Path, *, quick: bool) -> int:
    if output_root.exists():
        raise FileExistsError(f"Refusing to overwrite tracing benchmark suite: {output_root}")
    output_root.mkdir(parents=True)
    command_summaries: list[dict[str, Any]] = []
    for mode in TRACING_MODES:
        command = [
            sys.executable,
            "-m",
            "benchmarks.tracing_suite",
            "--worker-mode",
            mode,
            "--output-root",
            str(output_root / mode),
        ]
        if quick:
            command.append("--quick")
        environment = {
            **os.environ,
            "PYTHONHASHSEED": "0",
            "LC_ALL": "C",
            "LANG": "C",
            "TZ": "UTC",
        }
        completed = subprocess.run(command, check=True, env=environment)
        command_summaries.append(
            {
                "tracing_mode": mode,
                "command": [
                    Path(sys.executable).name,
                    "-m",
                    "benchmarks.tracing_suite",
                    "--worker-mode",
                    mode,
                    "--output-root",
                    f"<output-root>/{mode}",
                    *(["--quick"] if quick else []),
                ],
                "returncode": completed.returncode,
            }
        )
    summaries = [json.loads((output_root / mode / SUMMARY_FILE).read_text(encoding="utf-8")) for mode in TRACING_MODES]
    index = {
        "schema_version": 1,
        "canonical": not quick,
        "commands": command_summaries,
        "tracing_modes": list(TRACING_MODES),
        "workers": summaries,
        "validation": {
            "worker_count": len(summaries),
            "all_workers_succeeded": True,
            "sampled_consumer_spans_exported": next(
                worker for worker in summaries if worker["tracing_mode"] == "enabled-sampled-exported"
            )["exported_spans"]["names"].get("relayna.consumer.task_message", 0)
            > 0,
            "sampled_producer_spans_exported": any(
                name.startswith("relayna.rabbitmq.publish")
                for name in next(
                    worker for worker in summaries if worker["tracing_mode"] == "enabled-sampled-exported"
                )["exported_spans"]["names"]
            ),
            "unsampled_export_count": next(
                worker for worker in summaries if worker["tracing_mode"] == "enabled-unsampled"
            )["exported_spans"]["count"],
        },
    }
    write_text_artifact(output_root / SUMMARY_FILE, json.dumps(index, indent=2, sort_keys=True) + "\n")
    print(f"Completed tracing benchmark suite in {output_root.resolve()}")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run all registered Relayna benchmarks under three isolated tracing configurations."
    )
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--worker-mode", choices=TRACING_MODES)
    parser.add_argument("--quick", action="store_true", help="Use one repeat and one operation per size.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.worker_mode is not None:
        return _worker(args.worker_mode, args.output_root, quick=args.quick)
    return _parent(args.output_root, quick=args.quick)


if __name__ == "__main__":
    raise SystemExit(main())
