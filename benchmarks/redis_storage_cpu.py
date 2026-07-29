"""Benchmark Relayna CPU work immediately around Redis persistence."""

from __future__ import annotations

import argparse
import gc
import hashlib
import html
import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any, Literal, cast

from benchmarks.registry import BenchmarkDefinition, BenchmarkOutcome
from benchmarks.reporting import collect_environment, write_text_artifact
from relayna.dlq.models import DLQRecord
from relayna.observability.feed import RelaynaServiceEvent, ServiceEventSourceKind
from relayna.storage.task_lease_store import TaskLease
from relayna.storage.workflow_contract_store import build_dedup_signature

PayloadProfile = Literal["ascii", "unicode-numeric"]
OperationName = Literal["encode", "decode", "canonical-hash"]
Operation = Callable[[], object]
ValueBuilder = Callable[[PayloadProfile, str], object]
Serializer = Callable[[object], bytes]
Decoder = Callable[[bytes], object]
Hasher = Callable[[object], str]

TARGET_SIZES: dict[str, int] = {
    "1 KB": 1_024,
    "16 KB": 16_384,
    "128 KB": 131_072,
}
DEFAULT_ITERATIONS: dict[int, int] = {
    1_024: 3_000,
    16_384: 600,
    131_072: 80,
}
DEFAULT_REPEATS = 5
DEFAULT_OUTPUT = Path("reports/redis-storage-cpu-microbenchmarks.html")
_FIXED_TIMESTAMP = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
_UNICODE_FILLER_TOKEN = "สถานะ東京🚀"


@dataclass(frozen=True)
class DedupInput:
    """Inputs accepted by Relayna's canonical workflow dedup hash."""

    task_id: str
    action: str | None
    payload: Mapping[str, Any]
    dedup_key_fields: tuple[str, ...]


@dataclass(frozen=True)
class Representation:
    """One current Redis-facing representation and its supported operation."""

    name: str
    label: str
    family: str
    operations: tuple[OperationName, ...]
    build_value: ValueBuilder
    serialize: Serializer
    decode: Decoder | None = None
    canonical_hash: Hasher | None = None


@dataclass(frozen=True)
class BenchmarkFixture:
    """One exact-size deterministic input shared by matching matrix cases."""

    representation: Representation
    profile: PayloadProfile
    target_bytes: int
    value: object
    serialized: bytes


@dataclass(frozen=True)
class BenchmarkCase:
    """One cell in the Redis-facing CPU benchmark matrix."""

    representation: str
    representation_label: str
    family: str
    profile: PayloadProfile
    target_label: str
    target_bytes: int
    operation: OperationName
    iterations: int

    @property
    def is_baseline(self) -> bool:
        return self.profile == "ascii"


@dataclass(frozen=True)
class BenchmarkResult:
    """Measured output for one benchmark matrix case."""

    case: BenchmarkCase
    actual_bytes: int
    repeats: int
    sample_ns_per_op: tuple[float, ...]
    median_ns_per_op: float
    throughput_mb_s: float
    relative_to_ascii: float


def _profile_metadata(profile: PayloadProfile) -> dict[str, Any]:
    if profile == "ascii":
        return {"profile": "ascii", "label": "benchmark", "tags": ["alpha", "beta"]}
    return {
        "profile": "unicode-numeric",
        "label": "สถานะ-東京-🚀",
        "values": [0, -7, 3.14159265, 1_000_000_000],
    }


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _status_value(profile: PayloadProfile, content: str) -> dict[str, Any]:
    return {
        "task_id": "benchmark-task-0001",
        "status": "RUNNING",
        "timestamp": _FIXED_TIMESTAMP.isoformat(),
        "correlation_id": "benchmark-correlation",
        "meta": _profile_metadata(profile),
        "payload": {"content": content},
    }


def _observation_value(profile: PayloadProfile, content: str) -> dict[str, Any]:
    return {
        "task_id": "benchmark-task-0001",
        "event_type": "BenchmarkObservation",
        "component": "benchmark-worker",
        "timestamp": _FIXED_TIMESTAMP,
        "attributes": _profile_metadata(profile),
        "payload": {"content": content},
    }


def _dlq_value(profile: PayloadProfile, content: str) -> DLQRecord:
    profile_fields = {"p": "ascii", "n": 7} if profile == "ascii" else {"p": "東京🚀", "n": [0, -7, 3.125, 1_000_000]}
    return DLQRecord(
        dlq_id="bench-dlq",
        queue_name="dlq",
        source_queue_name="source",
        retry_queue_name="retry",
        task_id="bench-task",
        reason="benchmark",
        retry_attempt=2,
        max_retries=5,
        headers=profile_fields,
        body={"content": content},
        body_encoding="json",
        raw_body_b64="e30=",
        dead_lettered_at=_FIXED_TIMESTAMP,
    )


def _lease_value(profile: PayloadProfile, content: str) -> TaskLease:
    unicode_profile = profile == "unicode-numeric"
    return TaskLease(
        lease_id="benchmark-lease-0001",
        task_id="benchmark-task-0001",
        owner_id="benchmark-owner",
        consumer_name=("ผู้ใช้-東京-42-" if unicode_profile else "benchmark-consumer-") + content,
        acquired_at=_FIXED_TIMESTAMP,
        heartbeat_at=_FIXED_TIMESTAMP,
        expires_at=_FIXED_TIMESTAMP + timedelta(minutes=5),
        message_id="benchmark-message",
        task_type="งาน.処理.🚀" if unicode_profile else "benchmark.execute",
        attempt=42 if unicode_profile else 7,
    )


def _feed_value(profile: PayloadProfile, content: str) -> RelaynaServiceEvent:
    return RelaynaServiceEvent(
        cursor="benchmark-cursor-0001",
        task_id="benchmark-task-0001",
        event_type="status.running",
        source_kind=ServiceEventSourceKind.STATUS,
        component="benchmark-worker",
        timestamp=_FIXED_TIMESTAMP.isoformat(),
        correlation_id="benchmark-correlation",
        payload={
            "profile": _profile_metadata(profile),
            "content": content,
        },
    )


def _event_hash_value(profile: PayloadProfile, content: str) -> dict[str, Any]:
    return {
        "source_kind": "observation",
        "task_id": "benchmark-task-0001",
        "event_type": "BenchmarkObservation",
        "timestamp": _FIXED_TIMESTAMP,
        "profile": _profile_metadata(profile),
        "payload": {"content": content},
    }


def _dedup_value(profile: PayloadProfile, content: str) -> DedupInput:
    return DedupInput(
        task_id="benchmark-task-0001",
        action="benchmark.execute",
        payload={
            "content": content,
            "profile": _profile_metadata(profile),
        },
        dedup_key_fields=("content", "profile"),
    )


def _status_json(value: object) -> bytes:
    return json.dumps(cast(dict[str, Any], value), ensure_ascii=False).encode("utf-8")


def _observation_json(value: object) -> bytes:
    return json.dumps(cast(dict[str, Any], value), ensure_ascii=False, default=_json_default).encode("utf-8")


def _decode_json(payload: bytes) -> object:
    return json.loads(payload)


def _dlq_json(value: object) -> bytes:
    return cast(DLQRecord, value).model_dump_json().encode("utf-8")


def _decode_dlq(payload: bytes) -> DLQRecord:
    return DLQRecord.model_validate_json(payload)


def _lease_json(value: object) -> bytes:
    return cast(TaskLease, value).model_dump_json().encode("utf-8")


def _decode_lease(payload: bytes) -> TaskLease:
    return TaskLease.model_validate_json(payload.decode("utf-8"))


def _feed_json(value: object) -> bytes:
    return cast(RelaynaServiceEvent, value).model_dump_json().encode("utf-8")


def _decode_feed(payload: bytes) -> RelaynaServiceEvent:
    return RelaynaServiceEvent.model_validate_json(payload)


def _event_canonical_bytes(value: object) -> bytes:
    return json.dumps(
        cast(dict[str, Any], value),
        ensure_ascii=False,
        sort_keys=True,
        default=_json_default,
    ).encode("utf-8")


def _event_canonical_hash(value: object) -> str:
    return hashlib.sha256(_event_canonical_bytes(value)).hexdigest()


def _dedup_canonical_bytes(value: object) -> bytes:
    dedup = cast(DedupInput, value)
    material = {
        "task_id": dedup.task_id,
        "action": dedup.action,
        "fields": {field: dedup.payload.get(field) for field in dedup.dedup_key_fields},
    }
    return json.dumps(material, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _dedup_canonical_hash(value: object) -> str:
    dedup = cast(DedupInput, value)
    return build_dedup_signature(
        task_id=dedup.task_id,
        action=dedup.action,
        payload=dedup.payload,
        dedup_key_fields=dedup.dedup_key_fields,
    )


REPRESENTATIONS: tuple[Representation, ...] = (
    Representation(
        name="status-json",
        label="Generic status JSON",
        family="Generic JSON storage",
        operations=("encode", "decode"),
        build_value=_status_value,
        serialize=_status_json,
        decode=_decode_json,
    ),
    Representation(
        name="observation-json",
        label="Generic observation JSON",
        family="Generic JSON storage",
        operations=("encode", "decode"),
        build_value=_observation_value,
        serialize=_observation_json,
        decode=_decode_json,
    ),
    Representation(
        name="dlq-record",
        label="Pydantic DLQ record",
        family="Pydantic record storage",
        operations=("encode", "decode"),
        build_value=_dlq_value,
        serialize=_dlq_json,
        decode=_decode_dlq,
    ),
    Representation(
        name="lease-record",
        label="Pydantic task lease",
        family="Pydantic record storage",
        operations=("encode", "decode"),
        build_value=_lease_value,
        serialize=_lease_json,
        decode=_decode_lease,
    ),
    Representation(
        name="feed-record",
        label="Pydantic service-feed record",
        family="Pydantic record storage",
        operations=("encode", "decode"),
        build_value=_feed_value,
        serialize=_feed_json,
        decode=_decode_feed,
    ),
    Representation(
        name="event-hash",
        label="Canonical event hash",
        family="Canonical hashes",
        operations=("canonical-hash",),
        build_value=_event_hash_value,
        serialize=_event_canonical_bytes,
        canonical_hash=_event_canonical_hash,
    ),
    Representation(
        name="dedup-hash",
        label="Workflow dedup hash",
        family="Canonical hashes",
        operations=("canonical-hash",),
        build_value=_dedup_value,
        serialize=_dedup_canonical_bytes,
        canonical_hash=_dedup_canonical_hash,
    ),
)
_REPRESENTATIONS_BY_NAME = {representation.name: representation for representation in REPRESENTATIONS}


def _calibrated_content(
    representation: Representation,
    profile: PayloadProfile,
    target_bytes: int,
) -> str:
    empty_size = len(representation.serialize(representation.build_value(profile, "")))
    if empty_size > target_bytes:
        raise ValueError(
            f"Target {target_bytes} bytes is too small for {representation.name}/{profile}; minimum is {empty_size}."
        )
    if profile == "ascii":
        return "x" * (target_bytes - empty_size)

    low = 0
    high = target_bytes - empty_size + 1
    while low + 1 < high:
        middle = (low + high) // 2
        candidate = _UNICODE_FILLER_TOKEN * middle
        size = len(representation.serialize(representation.build_value(profile, candidate)))
        if size <= target_bytes:
            low = middle
        else:
            high = middle
    unicode_content = _UNICODE_FILLER_TOKEN * low
    unicode_size = len(representation.serialize(representation.build_value(profile, unicode_content)))
    return unicode_content + ("x" * (target_bytes - unicode_size))


def build_fixture(
    representation_name: str,
    profile: PayloadProfile,
    target_bytes: int,
) -> BenchmarkFixture:
    """Build one deterministic fixture with an exact processed-byte size."""

    try:
        representation = _REPRESENTATIONS_BY_NAME[representation_name]
    except KeyError as exc:
        raise ValueError(f"Unsupported representation: {representation_name}") from exc
    if profile not in {"ascii", "unicode-numeric"}:
        raise ValueError(f"Unsupported payload profile: {profile}")

    content = _calibrated_content(representation, profile, target_bytes)
    value = representation.build_value(profile, content)
    serialized = representation.serialize(value)
    if len(serialized) != target_bytes:
        raise RuntimeError(
            f"Fixture sizing failed for {representation.name}/{profile}: "
            f"expected {target_bytes} bytes, produced {len(serialized)} bytes."
        )
    return BenchmarkFixture(
        representation=representation,
        profile=profile,
        target_bytes=target_bytes,
        value=value,
        serialized=serialized,
    )


def build_matrix(iterations_by_size: Mapping[int, int] | None = None) -> list[BenchmarkCase]:
    """Return the complete deterministic Redis-facing CPU benchmark matrix."""

    iteration_counts = dict(DEFAULT_ITERATIONS if iterations_by_size is None else iterations_by_size)
    missing_sizes = set(TARGET_SIZES.values()) - iteration_counts.keys()
    if missing_sizes:
        raise ValueError(f"Missing iteration counts for byte sizes: {sorted(missing_sizes)}")
    if any(iteration_counts[size] < 1 for size in TARGET_SIZES.values()):
        raise ValueError("Iteration counts must be positive.")

    cases: list[BenchmarkCase] = []
    for representation in REPRESENTATIONS:
        for target_label, target_bytes in TARGET_SIZES.items():
            for operation in representation.operations:
                for profile in ("ascii", "unicode-numeric"):
                    cases.append(
                        BenchmarkCase(
                            representation=representation.name,
                            representation_label=representation.label,
                            family=representation.family,
                            profile=profile,
                            target_label=target_label,
                            target_bytes=target_bytes,
                            operation=operation,
                            iterations=iteration_counts[target_bytes],
                        )
                    )
    return cases


def _operation_for(case: BenchmarkCase, fixture: BenchmarkFixture) -> Operation:
    representation = fixture.representation
    if case.operation == "encode":
        return lambda: representation.serialize(fixture.value)
    if case.operation == "decode":
        decoder = representation.decode
        if decoder is None:
            raise RuntimeError(f"{representation.name} does not define decode.")
        return lambda: decoder(fixture.serialized)
    canonical_hash = representation.canonical_hash
    if canonical_hash is None:
        raise RuntimeError(f"{representation.name} does not define canonical hashing.")
    return lambda: canonical_hash(fixture.value)


def _assert_operation(case: BenchmarkCase, fixture: BenchmarkFixture, operation: Operation) -> None:
    output = operation()
    if case.operation == "encode":
        if output != fixture.serialized:
            raise RuntimeError(f"{case.representation} encode did not preserve the calibrated fixture.")
        return
    if case.operation == "decode":
        representation = fixture.representation
        if representation.serialize(output) != fixture.serialized:
            raise RuntimeError(f"{case.representation} decode did not round-trip the calibrated fixture.")
        return
    if not isinstance(output, str) or len(output) != 64:
        raise RuntimeError(f"{case.representation} did not produce a SHA-256 hexadecimal digest.")
    try:
        int(output, 16)
    except ValueError as exc:
        raise RuntimeError(f"{case.representation} produced a non-hexadecimal digest.") from exc


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
    """Execute the matrix and calculate timing, throughput, and profile ratios."""

    if repeats < 1:
        raise ValueError("Repeats must be positive.")
    cases = build_matrix(iterations_by_size)
    fixtures: dict[tuple[str, PayloadProfile, int], BenchmarkFixture] = {}
    operations: dict[BenchmarkCase, Operation] = {}

    for representation in REPRESENTATIONS:
        for profile in ("ascii", "unicode-numeric"):
            for target_bytes in TARGET_SIZES.values():
                key = (representation.name, profile, target_bytes)
                fixtures[key] = build_fixture(representation.name, profile, target_bytes)

    for case in cases:
        key = (case.representation, case.profile, case.target_bytes)
        operation = _operation_for(case, fixtures[key])
        _assert_operation(case, fixtures[key], operation)
        operations[case] = operation

    samples: dict[BenchmarkCase, list[float]] = {case: [] for case in cases}
    for repeat_index in range(repeats):
        offset = repeat_index % len(cases)
        round_cases = cases[offset:] + cases[:offset]
        if repeat_index % 2:
            round_cases.reverse()
        for case in round_cases:
            samples[case].append(_time_operation(operations[case], case.iterations))

    medians = {case: median(case_samples) for case, case_samples in samples.items()}
    ascii_medians = {
        (case.representation, case.target_bytes, case.operation): medians[case] for case in cases if case.is_baseline
    }
    results: list[BenchmarkResult] = []
    for case in cases:
        median_ns = medians[case]
        baseline_ns = ascii_medians[(case.representation, case.target_bytes, case.operation)]
        results.append(
            BenchmarkResult(
                case=case,
                actual_bytes=case.target_bytes,
                repeats=repeats,
                sample_ns_per_op=tuple(samples[case]),
                median_ns_per_op=median_ns,
                throughput_mb_s=(case.target_bytes / 1_000_000) / (median_ns / 1_000_000_000),
                relative_to_ascii=baseline_ns / median_ns,
            )
        )
    return results


def _render_result_rows(results: Sequence[BenchmarkResult]) -> str:
    rows: list[str] = []
    for result in results:
        case = result.case
        baseline_class = ' class="baseline"' if case.is_baseline else ""
        profile_label = "ASCII" if case.profile == "ascii" else "Unicode + numeric"
        rows.append(
            f"<tr{baseline_class}>"
            f"<td>{html.escape(case.family)}</td>"
            f"<td>{html.escape(case.representation_label)}</td>"
            f"<td>{html.escape(case.operation.title())}</td>"
            f"<td>{html.escape(profile_label)}</td>"
            f"<td>{html.escape(case.target_label)}</td>"
            f'<td class="number">{result.actual_bytes:,}</td>'
            f'<td class="number">{case.iterations:,} × {result.repeats}</td>'
            f'<td class="number">{result.median_ns_per_op / 1_000:,.2f}</td>'
            f'<td class="number">{result.throughput_mb_s:,.2f}</td>'
            f'<td class="number">{result.relative_to_ascii:,.2f}×</td>'
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
  <title>Relayna Redis-Facing CPU Microbenchmarks</title>
  <style>
    :root {{ color-scheme: light; --ink: #172033; --muted: #5d687a; --line: #d9deea;
      --panel: #f7f8fc; --accent: #3155a6; --baseline: #eef4ff; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: #fff; color: var(--ink);
      font: 15px/1.55 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    main {{ width: min(1600px, calc(100% - 32px)); margin: 36px auto 72px; }}
    h1 {{ margin-bottom: 4px; font-size: clamp(28px, 4vw, 44px); line-height: 1.1; }}
    h2 {{ margin-top: 38px; border-bottom: 1px solid var(--line); padding-bottom: 8px; }}
    p, li {{ max-width: 94ch; }}
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
  <h1>Relayna Redis-Facing CPU Microbenchmarks</h1>
  <p class="lede">Deterministic serialization, validated parsing, and canonical hashing
  immediately around Redis persistence, without Redis server or network variability.</p>

  <div class="summary">
    <div><strong>{len(results)}</strong>matrix cases</div>
    <div><strong>{len(REPRESENTATIONS)}</strong>representations</div>
    <div><strong>{len(TARGET_SIZES)}</strong>exact payload sizes</div>
    <div><strong>{total_operations:,}</strong>timed operations</div>
  </div>

  <h2>Methodology</h2>
  <p>The matrix covers generic status and observation JSON storage; Pydantic DLQ,
  task-lease, and merged service-feed records; service-event canonical hashes; and
  workflow dedup hashes. Storage representations measure encode and decode. Hash
  representations measure canonical JSON construction plus SHA-256.</p>
  <p>Every representation uses fixed identifiers and timestamps. ASCII fixtures use
  ASCII metadata and content. Unicode/numeric fixtures include Thai, Japanese, emoji,
  signed integers, floating-point values, and large integers. Deterministic content is
  calibrated so the exact UTF-8 bytes produced or consumed by every case equal 1,024,
  16,384, or 131,072 bytes. For hashes, the byte count is the canonical material passed
  to SHA-256, not the fixed-size hexadecimal digest.</p>
  <p>All operations mirror current Relayna behavior. Standard-library JSON cases use
  <code>json.dumps(..., ensure_ascii=False)</code> and <code>json.loads</code>.
  Observation and event cases use Relayna's datetime fallback. Pydantic records use
  <code>model_dump_json</code> and <code>model_validate_json</code>; lease decode also
  includes the current explicit UTF-8 decode. Workflow dedup uses
  <code>build_dedup_signature</code>.</p>
  <p>Every case passes an untimed semantic preflight. Displayed timing is the median of
  repeated <code>perf_counter_ns</code> samples, with garbage collection disabled only
  inside timed loops. Case order rotates and reverses between repeat rounds. Throughput
  uses decimal MB. “vs ASCII” is the matching ASCII median divided by the case median,
  so values above 1.00× indicate faster execution.</p>
  <p class="note">No Redis process, command execution, socket, network, or event-loop
  scheduling is measured. This benchmark isolates deterministic CPU cost and does not
  change or recommend changing Relayna's production storage formats.</p>

  <h2>Results</h2>
  <div class="table-wrap">
    <table>
      <thead><tr>
        <th>Family</th><th>Representation</th><th>Operation</th><th>Profile</th>
        <th>Target</th><th class="number">Actual bytes</th>
        <th class="number">Iterations × repeats</th><th class="number">Median µs/op</th>
        <th class="number">Throughput MB/s</th><th class="number">vs ASCII</th>
      </tr></thead>
      <tbody>
{result_rows}
      </tbody>
    </table>
  </div>

  <h2>Environment and reproducibility</h2>
  <p>From the repository root, rerun
  <code>uv run python -m benchmarks run redis-storage-cpu</code>. A successful run
  atomically replaces <code>reports/redis-storage-cpu-microbenchmarks.html</code>.</p>
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
    """Atomically write the rendered report."""

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
    """Add Redis-storage-specific options to the shared benchmark CLI."""

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
        help='Override iterations for a size, for example --iterations "128 KB=5".',
    )


def run_from_cli(args: argparse.Namespace) -> BenchmarkOutcome:
    """Run the Redis-facing CPU benchmark from shared-CLI arguments."""

    iterations = dict(DEFAULT_ITERATIONS)
    iterations.update(dict(cast(list[tuple[int, int]], args.iterations)))
    results = run_benchmarks(repeats=args.repeats, iterations_by_size=iterations)
    environment = collect_environment(
        package_names=("relayna", "pydantic", "pydantic-core", "redis"),
        extra={
            "Redis interaction": "none (CPU-only)",
            "Canonical hash": "SHA-256",
            "Payload profiles": "ASCII; Unicode + numeric",
        },
    )
    report_path = write_html_report(args.output, results, environment)
    return BenchmarkOutcome(artifacts=(report_path,), measurement_count=len(results))


BENCHMARK = BenchmarkDefinition(
    name="redis-storage-cpu",
    summary="Measure Redis-facing serialization, validation, and canonical hashing CPU cost.",
    default_output=DEFAULT_OUTPUT,
    add_arguments=add_cli_arguments,
    run=run_from_cli,
)
