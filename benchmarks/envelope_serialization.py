"""Benchmark Relayna task-envelope JSON serialization and validated parsing."""

from __future__ import annotations

import argparse
import gc
import html
import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from typing import Literal, cast

from benchmarks.registry import BenchmarkDefinition, BenchmarkOutcome
from benchmarks.reporting import collect_environment, write_text_artifact
from relayna.contracts import BatchTaskEnvelope, TaskEnvelope

Envelope = TaskEnvelope | BatchTaskEnvelope
EnvelopeKind = Literal["task", "batch"]
Direction = Literal["outbound", "inbound"]
Operation = Callable[[], object]

TARGET_SIZES: dict[str, int] = {
    "1 KB": 1_024,
    "16 KB": 16_384,
    "128 KB": 131_072,
    "1 MB": 1_048_576,
}
DEFAULT_ITERATIONS: dict[int, int] = {
    1_024: 4_000,
    16_384: 1_000,
    131_072: 150,
    1_048_576: 20,
}
DEFAULT_REPEATS = 5
DEFAULT_OUTPUT = Path("reports/envelope-microbenchmarks.html")
_FIXED_CREATED_AT = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
_BATCH_TASK_COUNT = 2


@dataclass(frozen=True)
class BenchmarkCase:
    """One cell in the envelope benchmark matrix."""

    envelope_kind: EnvelopeKind
    target_label: str
    target_bytes: int
    direction: Direction
    implementation: str
    implementation_label: str
    is_baseline: bool
    iterations: int


@dataclass(frozen=True)
class BenchmarkResult:
    """Measured output for one benchmark case."""

    case: BenchmarkCase
    actual_bytes: int
    repeats: int
    sample_ns_per_op: tuple[float, ...]
    median_ns_per_op: float
    throughput_mb_s: float
    relative_to_current: float


def _task_envelope(*, sequence: int, padding: str) -> TaskEnvelope:
    return TaskEnvelope(
        task_id=f"benchmark-task-{sequence:04d}",
        payload={
            "benchmark": "envelope-json",
            "sequence": sequence,
            "content": padding,
        },
        created_at=_FIXED_CREATED_AT,
        service="benchmark-service",
        task_type="benchmark.execute",
        correlation_id="benchmark-correlation",
        priority=128,
    )


def current_outbound(envelope: Envelope) -> bytes:
    """Mirror Relayna's current model-dump then stdlib-JSON encoding path."""

    payload = envelope.model_dump(mode="json", exclude_none=True)
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def pydantic_outbound(envelope: Envelope) -> bytes:
    """Encode directly through Pydantic's JSON serializer."""

    return envelope.model_dump_json(exclude_none=True).encode("utf-8")


def current_inbound(envelope_type: type[Envelope], payload: bytes) -> Envelope:
    """Mirror Relayna's current UTF-8 decode, stdlib parse, and validation path."""

    parsed = json.loads(payload.decode("utf-8", errors="replace"))
    return envelope_type.model_validate(parsed)


def pydantic_inbound(envelope_type: type[Envelope], payload: bytes) -> Envelope:
    """Parse and validate directly through Pydantic's JSON validator."""

    return envelope_type.model_validate_json(payload)


def build_fixture(envelope_kind: EnvelopeKind, target_bytes: int) -> Envelope:
    """Build a deterministic fixture with an exact current-wire byte size."""

    if envelope_kind == "task":
        empty = _task_envelope(sequence=1, padding="")
        padding_bytes = target_bytes - len(current_outbound(empty))
        if padding_bytes < 0:
            raise ValueError(f"Target {target_bytes} bytes is too small for a task fixture.")
        fixture: Envelope = _task_envelope(sequence=1, padding="x" * padding_bytes)
    elif envelope_kind == "batch":
        empty = BatchTaskEnvelope(
            batch_id="benchmark-batch-0001",
            tasks=[_task_envelope(sequence=sequence, padding="") for sequence in range(1, _BATCH_TASK_COUNT + 1)],
            meta={"benchmark": "envelope-json", "task_count": _BATCH_TASK_COUNT},
            created_at=_FIXED_CREATED_AT,
        )
        padding_bytes = target_bytes - len(current_outbound(empty))
        if padding_bytes < 0:
            raise ValueError(f"Target {target_bytes} bytes is too small for a batch fixture.")
        even_padding, remainder = divmod(padding_bytes, _BATCH_TASK_COUNT)
        fixture = BatchTaskEnvelope(
            batch_id="benchmark-batch-0001",
            tasks=[
                _task_envelope(
                    sequence=sequence,
                    padding="x" * (even_padding + (1 if sequence <= remainder else 0)),
                )
                for sequence in range(1, _BATCH_TASK_COUNT + 1)
            ],
            meta={"benchmark": "envelope-json", "task_count": _BATCH_TASK_COUNT},
            created_at=_FIXED_CREATED_AT,
        )
    else:
        raise ValueError(f"Unsupported envelope kind: {envelope_kind}")

    actual_bytes = len(current_outbound(fixture))
    if actual_bytes != target_bytes:
        raise RuntimeError(f"Fixture sizing failed: expected {target_bytes} bytes, produced {actual_bytes} bytes.")
    return fixture


def build_matrix(iterations_by_size: Mapping[int, int] | None = None) -> list[BenchmarkCase]:
    """Return the complete deterministic benchmark matrix."""

    iteration_counts = dict(DEFAULT_ITERATIONS if iterations_by_size is None else iterations_by_size)
    missing_sizes = set(TARGET_SIZES.values()) - iteration_counts.keys()
    if missing_sizes:
        raise ValueError(f"Missing iteration counts for byte sizes: {sorted(missing_sizes)}")
    if any(iteration_counts[size] < 1 for size in TARGET_SIZES.values()):
        raise ValueError("Iteration counts must be positive.")

    cases: list[BenchmarkCase] = []
    for envelope_kind in ("task", "batch"):
        for target_label, target_bytes in TARGET_SIZES.items():
            for direction in ("outbound", "inbound"):
                implementations = (
                    (
                        "current",
                        "Current: model_dump + stdlib JSON"
                        if direction == "outbound"
                        else "Current: decode + stdlib JSON + model_validate",
                        True,
                    ),
                    (
                        "pydantic-direct",
                        "Pydantic: model_dump_json" if direction == "outbound" else "Pydantic: model_validate_json",
                        False,
                    ),
                )
                for implementation, implementation_label, is_baseline in implementations:
                    cases.append(
                        BenchmarkCase(
                            envelope_kind=envelope_kind,
                            target_label=target_label,
                            target_bytes=target_bytes,
                            direction=direction,
                            implementation=implementation,
                            implementation_label=implementation_label,
                            is_baseline=is_baseline,
                            iterations=iteration_counts[target_bytes],
                        )
                    )
    return cases


def _assert_equivalent(
    envelope: Envelope,
    envelope_type: type[Envelope],
    baseline_bytes: bytes,
) -> None:
    baseline_model = current_inbound(envelope_type, baseline_bytes)
    direct_model = pydantic_inbound(envelope_type, baseline_bytes)
    expected = envelope.model_dump(mode="json", exclude_none=True)
    if baseline_model.model_dump(mode="json", exclude_none=True) != expected:
        raise RuntimeError("Current inbound implementation did not preserve the fixture.")
    if direct_model.model_dump(mode="json", exclude_none=True) != expected:
        raise RuntimeError("Pydantic inbound implementation did not preserve the fixture.")
    direct_outbound_model = current_inbound(envelope_type, pydantic_outbound(envelope))
    if direct_outbound_model.model_dump(mode="json", exclude_none=True) != expected:
        raise RuntimeError("Pydantic outbound implementation did not preserve the fixture.")


def _operation_for(
    case: BenchmarkCase,
    envelope: Envelope,
    envelope_type: type[Envelope],
    baseline_bytes: bytes,
) -> tuple[Operation, int]:
    if case.direction == "outbound":
        if case.implementation == "current":
            return lambda: current_outbound(envelope), len(baseline_bytes)
        direct_bytes = pydantic_outbound(envelope)
        return lambda: pydantic_outbound(envelope), len(direct_bytes)
    if case.implementation == "current":
        return lambda: current_inbound(envelope_type, baseline_bytes), len(baseline_bytes)
    return lambda: pydantic_inbound(envelope_type, baseline_bytes), len(baseline_bytes)


def _time_operation(operation: Operation, iterations: int) -> float:
    gc_was_enabled = gc.isenabled()
    if gc_was_enabled:
        gc.disable()
    try:
        started_ns = time.perf_counter_ns()
        for _ in range(iterations):
            operation()
        elapsed_ns = time.perf_counter_ns() - started_ns
    finally:
        if gc_was_enabled:
            gc.enable()
    return elapsed_ns / iterations


def run_benchmarks(
    *,
    repeats: int = DEFAULT_REPEATS,
    iterations_by_size: Mapping[int, int] | None = None,
) -> list[BenchmarkResult]:
    """Execute the benchmark matrix and calculate comparable result metrics."""

    if repeats < 1:
        raise ValueError("Repeats must be positive.")
    cases = build_matrix(iterations_by_size)
    fixtures: dict[tuple[EnvelopeKind, int], Envelope] = {}
    baseline_payloads: dict[tuple[EnvelopeKind, int], bytes] = {}
    operations: dict[BenchmarkCase, tuple[Operation, int]] = {}

    for envelope_kind in ("task", "batch"):
        envelope_type: type[Envelope] = TaskEnvelope if envelope_kind == "task" else BatchTaskEnvelope
        for target_bytes in TARGET_SIZES.values():
            fixture = build_fixture(envelope_kind, target_bytes)
            baseline_bytes = current_outbound(fixture)
            fixture_key = (envelope_kind, target_bytes)
            fixtures[fixture_key] = fixture
            baseline_payloads[fixture_key] = baseline_bytes
            _assert_equivalent(fixture, envelope_type, baseline_bytes)

    for case in cases:
        key = (case.envelope_kind, case.target_bytes)
        envelope_type = TaskEnvelope if case.envelope_kind == "task" else BatchTaskEnvelope
        operations[case] = _operation_for(case, fixtures[key], envelope_type, baseline_payloads[key])

    for operation, _actual_bytes in operations.values():
        operation()

    samples: dict[BenchmarkCase, list[float]] = {case: [] for case in cases}
    for repeat_index in range(repeats):
        offset = repeat_index % len(cases)
        round_cases = cases[offset:] + cases[:offset]
        if repeat_index % 2:
            round_cases.reverse()
        for case in round_cases:
            operation, _actual_bytes = operations[case]
            samples[case].append(_time_operation(operation, case.iterations))

    medians = {case: median(case_samples) for case, case_samples in samples.items()}
    baseline_medians = {
        (case.envelope_kind, case.target_bytes, case.direction): medians[case] for case in cases if case.is_baseline
    }
    results: list[BenchmarkResult] = []
    for case in cases:
        _operation, actual_bytes = operations[case]
        median_ns = medians[case]
        baseline_ns = baseline_medians[(case.envelope_kind, case.target_bytes, case.direction)]
        results.append(
            BenchmarkResult(
                case=case,
                actual_bytes=actual_bytes,
                repeats=repeats,
                sample_ns_per_op=tuple(samples[case]),
                median_ns_per_op=median_ns,
                throughput_mb_s=(actual_bytes / 1_000_000) / (median_ns / 1_000_000_000),
                relative_to_current=baseline_ns / median_ns,
            )
        )
    return results


def _format_bytes(value: int) -> str:
    return f"{value:,}"


def _render_result_rows(results: Sequence[BenchmarkResult]) -> str:
    rows: list[str] = []
    for result in results:
        case = result.case
        baseline_class = ' class="baseline"' if case.is_baseline else ""
        rows.append(
            f"<tr{baseline_class}>"
            f"<td>{html.escape(case.envelope_kind.title())}</td>"
            f"<td>{html.escape(case.target_label)}</td>"
            f"<td>{html.escape(case.direction.title())}</td>"
            f"<td>{html.escape(case.implementation_label)}</td>"
            f'<td class="number">{_format_bytes(result.actual_bytes)}</td>'
            f'<td class="number">{case.iterations:,} × {result.repeats}</td>'
            f'<td class="number">{result.median_ns_per_op / 1_000:,.2f}</td>'
            f'<td class="number">{result.throughput_mb_s:,.2f}</td>'
            f'<td class="number">{result.relative_to_current:,.2f}×</td>'
            "</tr>"
        )
    return "\n".join(rows)


def render_html(results: Sequence[BenchmarkResult], environment: Mapping[str, str]) -> str:
    """Render a self-contained human-readable HTML benchmark report."""

    if not results:
        raise ValueError("At least one benchmark result is required.")
    metadata_rows = "\n".join(
        f"<tr><th>{html.escape(key)}</th><td>{html.escape(value)}</td></tr>" for key, value in environment.items()
    )
    result_rows = _render_result_rows(results)
    total_operations = sum(result.case.iterations * result.repeats for result in results)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Relayna Envelope Serialization Microbenchmarks</title>
  <style>
    :root {{ color-scheme: light; --ink: #172033; --muted: #5d687a; --line: #d9deea;
      --panel: #f7f8fc; --accent: #3155a6; --baseline: #eef4ff; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: #fff; color: var(--ink);
      font: 15px/1.55 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    main {{ width: min(1500px, calc(100% - 32px)); margin: 36px auto 72px; }}
    h1 {{ margin-bottom: 4px; font-size: clamp(28px, 4vw, 44px); line-height: 1.1; }}
    h2 {{ margin-top: 38px; border-bottom: 1px solid var(--line); padding-bottom: 8px; }}
    p, li {{ max-width: 90ch; }}
    .lede {{ color: var(--muted); font-size: 17px; }}
    .summary {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px; margin: 26px 0; }}
    .summary div {{ border: 1px solid var(--line); border-radius: 10px; padding: 15px;
      background: var(--panel); }}
    .summary strong {{ display: block; color: var(--accent); font-size: 22px; }}
    .table-wrap {{ overflow-x: auto; border: 1px solid var(--line); border-radius: 10px; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ padding: 9px 11px; border-bottom: 1px solid var(--line); text-align: left;
      vertical-align: top; white-space: nowrap; }}
    thead th {{ position: sticky; top: 0; background: #172033; color: #fff; }}
    tbody tr:last-child td, tbody tr:last-child th {{ border-bottom: 0; }}
    tbody tr.baseline {{ background: var(--baseline); }}
    .number {{ text-align: right; font-variant-numeric: tabular-nums; }}
    code {{ background: #edf0f7; border-radius: 4px; padding: 2px 5px; }}
    .note {{ padding: 14px 16px; border-left: 4px solid var(--accent); background: var(--panel); }}
    @media print {{ main {{ width: 100%; margin: 0; }} thead th {{ position: static; }} }}
  </style>
</head>
<body>
<main>
  <h1>Relayna Envelope Serialization Microbenchmarks</h1>
  <p class="lede">Deterministic task and batch envelope JSON encoding and validated parsing,
  measured without changing Relayna production behavior.</p>

  <div class="summary">
    <div><strong>{len(results)}</strong>matrix cases</div>
    <div><strong>{len(TARGET_SIZES)}</strong>target wire sizes</div>
    <div><strong>{total_operations:,}</strong>timed operations</div>
    <div><strong>µs/op</strong>primary timing unit</div>
  </div>

  <h2>Methodology</h2>
  <p>Fixtures use fixed identifiers, a fixed UTC timestamp, stable Relayna-shaped metadata,
  and deterministic ASCII padding. For each envelope kind, the current Relayna outbound
  pipeline is calibrated to exactly 1,024, 16,384, 131,072, and 1,048,576 bytes. The
  “Actual bytes” column reports the bytes processed or emitted by each case, so compact
  serializers are visible rather than normalized away.</p>
  <p>Outbound baseline: <code>model_dump(mode="json", exclude_none=True)</code>, then
  <code>json.dumps(..., ensure_ascii=False).encode("utf-8")</code>. Outbound comparison:
  <code>model_dump_json(exclude_none=True).encode("utf-8")</code>. Inbound baseline:
  UTF-8 decode with replacement, <code>json.loads</code>, then
  <code>Envelope.model_validate</code>. Inbound comparison:
  <code>Envelope.model_validate_json</code>. Both inbound implementations receive the
  identical current-baseline byte payload.</p>
  <p>Every implementation is checked for model equivalence before timing. Cases receive
  one untimed warm-up call. Each displayed value is the median of repeated
  <code>perf_counter_ns</code> samples, with garbage collection disabled only inside each
  timed loop. Case order rotates and reverses between repeat rounds. Throughput uses
  decimal MB (1,000,000 bytes). “vs current” is current median divided by case median, so
  values above 1.00× indicate faster execution.</p>
  <p class="note">These measurements compare available implementations; they do not change
  or recommend changing Relayna runtime behavior. Results are local-machine observations,
  not cross-machine performance guarantees.</p>

  <h2>Results</h2>
  <div class="table-wrap">
    <table>
      <thead><tr>
        <th>Envelope</th><th>Target</th><th>Direction</th><th>Implementation</th>
        <th class="number">Actual bytes</th><th class="number">Iterations × repeats</th>
        <th class="number">Median µs/op</th><th class="number">Throughput MB/s</th>
        <th class="number">vs current</th>
      </tr></thead>
      <tbody>
{result_rows}
      </tbody>
    </table>
  </div>

  <h2>Environment and reproducibility</h2>
  <p>From the repository root, rerun
  <code>uv run python -m benchmarks run envelope-serialization</code>. A successful run
  atomically replaces <code>reports/envelope-microbenchmarks.html</code>.</p>
  <div class="table-wrap"><table><tbody>
{metadata_rows}
  </tbody></table></div>
</main>
</body>
</html>
"""


def write_html_report(
    output_path: Path,
    results: Sequence[BenchmarkResult],
    environment: Mapping[str, str],
) -> Path:
    """Atomically write a rendered report to the requested path."""

    return write_text_artifact(output_path, render_html(results, environment))


def _parse_iteration_override(value: str) -> tuple[int, int]:
    try:
        label, count_text = value.split("=", maxsplit=1)
        target_bytes = TARGET_SIZES[label]
        count = int(count_text)
    except (KeyError, ValueError) as exc:
        labels = ", ".join(TARGET_SIZES)
        raise argparse.ArgumentTypeError(f"Use LABEL=COUNT with a label from: {labels}") from exc
    if count < 1:
        raise argparse.ArgumentTypeError("Iteration count must be positive.")
    return target_bytes, count


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Value must be a positive integer.") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("Value must be a positive integer.")
    return parsed


def add_cli_arguments(parser: argparse.ArgumentParser) -> None:
    """Add envelope-specific options to the shared benchmark CLI."""

    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"HTML output path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--repeats",
        type=_positive_int,
        default=DEFAULT_REPEATS,
        help=f"Timed samples per matrix case (default: {DEFAULT_REPEATS})",
    )
    parser.add_argument(
        "--iterations",
        action="append",
        default=[],
        type=_parse_iteration_override,
        metavar="LABEL=COUNT",
        help='Override iterations for a size, for example --iterations "1 MB=5".',
    )


def run_from_cli(args: argparse.Namespace) -> BenchmarkOutcome:
    """Run the envelope benchmark from parsed shared-CLI arguments."""

    iterations = dict(DEFAULT_ITERATIONS)
    iterations.update(dict(cast(list[tuple[int, int]], args.iterations)))
    results = run_benchmarks(repeats=args.repeats, iterations_by_size=iterations)
    report_path = write_html_report(args.output, results, collect_environment())
    return BenchmarkOutcome(artifacts=(report_path,), measurement_count=len(results))


BENCHMARK = BenchmarkDefinition(
    name="envelope-serialization",
    summary="Measure task-envelope JSON encoding and validated parsing.",
    default_output=DEFAULT_OUTPUT,
    add_arguments=add_cli_arguments,
    run=run_from_cli,
)
