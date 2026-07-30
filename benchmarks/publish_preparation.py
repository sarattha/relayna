"""Benchmark Relayna's complete local CPU-side AMQP publish preparation path."""

from __future__ import annotations

import argparse
import asyncio
import gc
import html
import json
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from typing import Any, Literal, cast

from aio_pika import Message
from pydantic import BaseModel

from benchmarks.registry import BenchmarkDefinition, BenchmarkOutcome
from benchmarks.reporting import collect_environment, write_text_artifact
from relayna._async import map_bounded
from relayna._transport_json import encode_transport_json
from relayna.contracts import (
    BatchTaskEnvelope,
    ContractAliasConfig,
    StatusEventEnvelope,
    TaskEnvelope,
    WorkflowEnvelope,
)
from relayna.contracts import task as task_contracts
from relayna.metrics import RelaynaMetrics
from relayna.rabbitmq.client import RelaynaRabbitClient
from relayna.topology import (
    RoutedTasksSharedStatusTopology,
    SharedStatusWorkflowTopology,
    SharedTasksSharedStatusTopology,
    WorkflowStage,
)

MessageKind = Literal["individual-task", "batch-envelope", "workflow", "status"]
InputKind = Literal["model", "canonical-mapping", "alias-mapping"]
TopologyKind = Literal["direct-shared", "task-type-routed", "workflow-stage"]
BenchmarkTopology = SharedTasksSharedStatusTopology | RoutedTasksSharedStatusTopology | SharedStatusWorkflowTopology
AsyncOperation = Callable[[], Awaitable[None]]

TARGET_SIZES: dict[str, int] = {
    "1 KB": 1_024,
    "16 KB": 16_384,
    "128 KB": 131_072,
    "1 MB": 1_048_576,
}
DEFAULT_ITERATIONS: dict[int, int] = {
    1_024: 3_000,
    16_384: 700,
    131_072: 80,
    1_048_576: 10,
}
DEFAULT_REPEATS = 5
DEFAULT_OUTPUT = Path("reports/publish-preparation.html")
TASKS_PER_OPERATION = 2
_FIXED_TIMESTAMP = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
_ALIAS_CONFIG = ContractAliasConfig(field_aliases={"task_id": "attempt_id"})
_EMBEDDED_DATA_PREFIX = "<!-- relayna-publish-preparation-data:"
_EMBEDDED_DATA_SUFFIX = ":end -->"


class _FixedDateTime(datetime):
    @classmethod
    def now(cls, tz: Any = None) -> datetime:
        return _FIXED_TIMESTAMP if tz is not None else _FIXED_TIMESTAMP.replace(tzinfo=None)


@dataclass(frozen=True)
class BenchmarkCase:
    """One deterministic publish-preparation matrix cell."""

    message_kind: MessageKind
    input_kind: InputKind
    topology: TopologyKind
    target_label: str
    target_bytes: int
    iterations: int


@dataclass(frozen=True)
class BenchmarkFixture:
    """Prepared benchmark input whose emitted body has an exact target size."""

    case: BenchmarkCase
    value: BaseModel | Mapping[str, Any] | tuple[BaseModel | Mapping[str, Any], ...]
    actual_message_bytes: int
    bytes_per_operation: int
    publications_per_operation: int
    tasks_prepared_per_operation: int


@dataclass(frozen=True)
class BenchmarkResult:
    """Timing and count results for one matrix case."""

    case: BenchmarkCase
    actual_message_bytes: int
    bytes_per_operation: int
    publications_per_operation: int
    preparations_per_operation: int
    repeats: int
    sample_ns_per_operation: tuple[float, ...]
    median_ns_per_operation: float
    median_absolute_deviation_ns: float
    operations_per_second: float
    throughput_mib_per_second: float
    total_operations: int
    total_prepared: int
    total_published: int
    relative_speedup: float | None = None


class NoOpExchange:
    """Deterministic async AMQP boundary that retains only the latest publish."""

    def __init__(self) -> None:
        self.published_count = 0
        self.active_publishes = 0
        self.peak_active_publishes = 0
        self.last_message: Message | None = None
        self.last_routing_key: str | None = None

    async def publish(self, message: Message, *, routing_key: str) -> None:
        self.active_publishes += 1
        self.peak_active_publishes = max(self.peak_active_publishes, self.active_publishes)
        try:
            self.published_count += 1
            self.last_message = message
            self.last_routing_key = routing_key
        finally:
            self.active_publishes -= 1


class PreparationCountingClient(RelaynaRabbitClient):
    """Untimed probe that counts the production task-preparation implementation."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.task_preparation_count = 0

    def _prepare_task_envelope(self, task: BaseModel | Mapping[str, Any]) -> TaskEnvelope:
        self.task_preparation_count += 1
        return super()._prepare_task_envelope(task)


class LegacyPreparationClient(RelaynaRabbitClient):
    """Benchmark-only reproduction of the starting duplicate-preparation paths."""

    async def publish_tasks(
        self,
        tasks: Sequence[BaseModel | Mapping[str, Any]],
        *,
        mode: Literal["individual", "batch_envelope"] = "individual",
        batch_id: str | None = None,
        meta: Mapping[str, Any] | None = None,
        max_concurrency: int = 16,
    ) -> None:
        prepared_tasks = [self._prepare_task_payload(task) for task in tasks]
        if mode == "individual":
            await map_bounded(prepared_tasks, self.publish_task, concurrency=max_concurrency)
            return
        if mode != "batch_envelope":
            raise ValueError(f"Unsupported publish mode '{mode}'.")
        if not batch_id or not str(batch_id).strip():
            raise ValueError("batch_id is required when mode='batch_envelope'.")
        envelope = BatchTaskEnvelope(
            batch_id=str(batch_id),
            tasks=[TaskEnvelope.model_validate(task) for task in prepared_tasks],
            meta=dict(meta or {}),
        )
        await self._publish_batch_envelope(
            envelope.model_dump(mode="json", exclude_none=True),
            priority=self._resolve_batch_priority(prepared_tasks),
        )


class LegacyCountingPreparationClient(PreparationCountingClient, LegacyPreparationClient):
    """Legacy benchmark path plus the untimed real-preparation counter."""


def build_matrix(iterations_by_size: Mapping[int, int] | None = None) -> list[BenchmarkCase]:
    """Return the complete bounded publish-preparation matrix."""

    iteration_counts = dict(DEFAULT_ITERATIONS if iterations_by_size is None else iterations_by_size)
    missing_sizes = set(TARGET_SIZES.values()) - iteration_counts.keys()
    if missing_sizes:
        raise ValueError(f"Missing iteration counts for byte sizes: {sorted(missing_sizes)}")
    if any(iteration_counts[size] < 1 for size in TARGET_SIZES.values()):
        raise ValueError("Iteration counts must be positive.")

    profiles: tuple[tuple[MessageKind, TopologyKind], ...] = (
        ("individual-task", "direct-shared"),
        ("individual-task", "task-type-routed"),
        ("batch-envelope", "direct-shared"),
        ("batch-envelope", "task-type-routed"),
        ("workflow", "workflow-stage"),
        ("status", "direct-shared"),
    )
    return [
        BenchmarkCase(
            message_kind=message_kind,
            input_kind=input_kind,
            topology=topology,
            target_label=target_label,
            target_bytes=target_bytes,
            iterations=iteration_counts[target_bytes],
        )
        for message_kind, topology in profiles
        for input_kind in ("model", "canonical-mapping", "alias-mapping")
        for target_label, target_bytes in TARGET_SIZES.items()
    ]


def _task_model(sequence: int, content: str) -> TaskEnvelope:
    return TaskEnvelope(
        task_id=f"benchmark-task-{sequence:04d}",
        payload={"benchmark": "publish-preparation", "content": content},
        created_at=_FIXED_TIMESTAMP,
        service="benchmark",
        task_type="benchmark.execute",
        correlation_id=f"benchmark-correlation-{sequence:04d}",
        priority=4,
    )


def _workflow_model(content: str) -> WorkflowEnvelope:
    return WorkflowEnvelope(
        task_id="benchmark-task-0001",
        message_id="benchmark-message-0001",
        correlation_id="benchmark-correlation-0001",
        stage="benchmark-stage",
        action="benchmark.execute",
        payload={"benchmark": "publish-preparation", "content": content},
        meta={"profile": "deterministic"},
        priority=4,
    )


def _status_model(content: str) -> StatusEventEnvelope:
    return StatusEventEnvelope(
        task_id="benchmark-task-0001",
        status="processing",
        timestamp=_FIXED_TIMESTAMP,
        message=content,
        meta={"benchmark": "publish-preparation"},
        correlation_id="benchmark-correlation-0001",
        event_id="benchmark-event-0001",
        service="benchmark",
    )


def _canonical_body(
    message_kind: MessageKind,
    content: str,
    *,
    adjustment: int = 0,
) -> tuple[bytes, tuple[BaseModel, ...]]:
    if message_kind == "individual-task":
        models = tuple(
            _task_model(sequence, content + ("x" * adjustment if sequence == 1 else ""))
            for sequence in range(1, TASKS_PER_OPERATION + 1)
        )
        return encode_transport_json(models[0].model_dump(mode="json", exclude_none=True)), models
    if message_kind == "batch-envelope":
        models = tuple(
            _task_model(sequence, content + ("x" * adjustment if sequence == 1 else ""))
            for sequence in range(1, TASKS_PER_OPERATION + 1)
        )
        envelope = BatchTaskEnvelope(
            batch_id="benchmark-batch-0001",
            tasks=list(models),
            meta={"benchmark": "publish-preparation"},
            created_at=_FIXED_TIMESTAMP,
        )
        return encode_transport_json(envelope.model_dump(mode="json", exclude_none=True)), models
    if message_kind == "workflow":
        workflow = _workflow_model(content)
        return encode_transport_json(workflow.as_transport_dict()), (workflow,)
    status = _status_model(content)
    return encode_transport_json(status.as_transport_dict()), (status,)


def _calibrated_content(message_kind: MessageKind, target_bytes: int) -> tuple[str, tuple[BaseModel, ...]]:
    empty_body, _ = _canonical_body(message_kind, "")
    if len(empty_body) > target_bytes:
        raise ValueError(f"Target {target_bytes} bytes is too small for {message_kind}; minimum is {len(empty_body)}.")
    low = 0
    high = target_bytes - len(empty_body) + 1
    while low + 1 < high:
        middle = (low + high) // 2
        body, _ = _canonical_body(message_kind, "x" * middle)
        if len(body) <= target_bytes:
            low = middle
        else:
            high = middle
    content = "x" * low
    body, models = _canonical_body(message_kind, content)
    if len(body) != target_bytes:
        body, models = _canonical_body(
            message_kind,
            content,
            adjustment=target_bytes - len(body),
        )
    if len(body) != target_bytes:
        raise RuntimeError(f"Fixture sizing failed for {message_kind}: expected {target_bytes}, produced {len(body)}.")
    return content, models


def _mapping_for(model: BaseModel, *, alias: bool) -> dict[str, Any]:
    if isinstance(model, WorkflowEnvelope | StatusEventEnvelope):
        payload = model.as_transport_dict()
    else:
        payload = model.model_dump(mode="json", exclude_none=True)
    if alias:
        payload["attempt_id"] = payload.pop("task_id")
    return payload


def build_fixture(case: BenchmarkCase) -> BenchmarkFixture:
    """Build a deterministic public input that emits exact-sized AMQP bodies."""

    _content, models = _calibrated_content(case.message_kind, case.target_bytes)
    alias = case.input_kind == "alias-mapping"
    if case.input_kind == "model":
        values: tuple[BaseModel | Mapping[str, Any], ...] = models
    else:
        values = tuple(_mapping_for(model, alias=alias) for model in models)

    if case.message_kind in {"individual-task", "batch-envelope"}:
        value: BaseModel | Mapping[str, Any] | tuple[BaseModel | Mapping[str, Any], ...] = values
        publications = TASKS_PER_OPERATION if case.message_kind == "individual-task" else 1
        preparations = TASKS_PER_OPERATION
        bytes_per_operation = (
            case.target_bytes * TASKS_PER_OPERATION if case.message_kind == "individual-task" else case.target_bytes
        )
    else:
        value = values[0]
        publications = 1
        preparations = 0
        bytes_per_operation = case.target_bytes
    return BenchmarkFixture(
        case=case,
        value=value,
        actual_message_bytes=case.target_bytes,
        bytes_per_operation=bytes_per_operation,
        publications_per_operation=publications,
        tasks_prepared_per_operation=preparations,
    )


def _topology(topology_kind: TopologyKind) -> BenchmarkTopology:
    if topology_kind == "direct-shared":
        return SharedTasksSharedStatusTopology(
            rabbitmq_url="amqp://guest:guest@localhost:5672/",
            tasks_exchange="benchmark.tasks.exchange",
            tasks_queue="benchmark.tasks.queue",
            tasks_routing_key="benchmark.task.request",
            status_exchange="benchmark.status.exchange",
            status_queue="benchmark.status.queue",
            task_max_priority=8,
        )
    if topology_kind == "task-type-routed":
        return RoutedTasksSharedStatusTopology(
            rabbitmq_url="amqp://guest:guest@localhost:5672/",
            tasks_exchange="benchmark.tasks.exchange",
            tasks_queue="benchmark.tasks.queue",
            task_types=("benchmark.execute",),
            status_exchange="benchmark.status.exchange",
            status_queue="benchmark.status.queue",
            task_max_priority=8,
        )
    return SharedStatusWorkflowTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        workflow_exchange="benchmark.workflow.exchange",
        status_exchange="benchmark.status.exchange",
        status_queue="benchmark.status.queue",
        workflow_max_priority=8,
        stages=(
            WorkflowStage(
                name="benchmark-stage",
                queue="benchmark.workflow.queue",
                binding_keys=("benchmark.stage.in",),
                publish_routing_key="benchmark.stage.in",
            ),
        ),
    )


def _client_for(
    case: BenchmarkCase,
    *,
    counting: bool = False,
    legacy_duplicate_preparation: bool = False,
) -> tuple[RelaynaRabbitClient, NoOpExchange]:
    if legacy_duplicate_preparation:
        client_type = LegacyCountingPreparationClient if counting else LegacyPreparationClient
    else:
        client_type = PreparationCountingClient if counting else RelaynaRabbitClient
    client = client_type(
        cast(Any, _topology(case.topology)),
        alias_config=_ALIAS_CONFIG,
        metrics=RelaynaMetrics(service="publish-preparation-benchmark"),
    )
    exchange = NoOpExchange()
    client._initialized = True
    if case.message_kind == "status":
        client._status_exchange = cast(Any, exchange)
    elif case.message_kind == "workflow":
        client._workflow_exchange = cast(Any, exchange)
    else:
        client._tasks_exchange = cast(Any, exchange)
    return client, exchange


def _operation_for(
    fixture: BenchmarkFixture,
    *,
    counting: bool = False,
    legacy_duplicate_preparation: bool = False,
) -> tuple[AsyncOperation, RelaynaRabbitClient, NoOpExchange]:
    client, exchange = _client_for(
        fixture.case,
        counting=counting,
        legacy_duplicate_preparation=legacy_duplicate_preparation,
    )
    case = fixture.case
    if case.message_kind == "individual-task":
        tasks = cast(tuple[BaseModel | Mapping[str, Any], ...], fixture.value)

        async def operation() -> None:
            await client.publish_tasks(tasks, mode="individual", max_concurrency=2)

    elif case.message_kind == "batch-envelope":
        tasks = cast(tuple[BaseModel | Mapping[str, Any], ...], fixture.value)

        async def operation() -> None:
            await client.publish_tasks(
                tasks,
                mode="batch_envelope",
                batch_id="benchmark-batch-0001",
                meta={"benchmark": "publish-preparation"},
            )

    elif case.message_kind == "workflow":
        payload = cast(BaseModel | Mapping[str, Any], fixture.value)

        async def operation() -> None:
            await client.publish_workflow(cast(Any, payload))

    else:
        event = cast(BaseModel | Mapping[str, Any], fixture.value)

        async def operation() -> None:
            await client.publish_status(event)

    return operation, client, exchange


async def _run_iterations(operation: AsyncOperation, iterations: int) -> None:
    for _ in range(iterations):
        await operation()


def _time_operation(loop: asyncio.AbstractEventLoop, operation: AsyncOperation, iterations: int) -> float:
    gc_was_enabled = gc.isenabled()
    if gc_was_enabled:
        gc.disable()
    try:
        started_ns = time.perf_counter_ns()
        loop.run_until_complete(_run_iterations(operation, iterations))
        elapsed_ns = time.perf_counter_ns() - started_ns
    finally:
        if gc_was_enabled:
            gc.enable()
    return elapsed_ns / iterations


def _measure_preparations(
    loop: asyncio.AbstractEventLoop,
    fixture: BenchmarkFixture,
    *,
    legacy_duplicate_preparation: bool,
) -> int:
    operation, client, exchange = _operation_for(
        fixture,
        counting=True,
        legacy_duplicate_preparation=legacy_duplicate_preparation,
    )
    loop.run_until_complete(operation())
    if exchange.published_count != fixture.publications_per_operation:
        raise RuntimeError(
            f"{fixture.case.message_kind} expected {fixture.publications_per_operation} publications, "
            f"observed {exchange.published_count}."
        )
    if exchange.last_message is None or len(exchange.last_message.body) != fixture.actual_message_bytes:
        actual_bytes = None if exchange.last_message is None else len(exchange.last_message.body)
        raise RuntimeError(
            f"{fixture.case.message_kind} expected a {fixture.actual_message_bytes}-byte AMQP body, "
            f"observed {actual_bytes}."
        )
    return cast(PreparationCountingClient, client).task_preparation_count


def run_benchmarks(
    *,
    repeats: int = DEFAULT_REPEATS,
    iterations_by_size: Mapping[int, int] | None = None,
    baseline_results: Sequence[BenchmarkResult] | None = None,
    legacy_duplicate_preparation: bool = False,
) -> list[BenchmarkResult]:
    """Run every case using one event loop and calculate counts and dispersion."""

    if repeats < 1:
        raise ValueError("Repeats must be positive.")
    cases = build_matrix(iterations_by_size)
    fixtures = {case: build_fixture(case) for case in cases}
    baseline_by_identity = {_case_identity(result.case): result for result in (baseline_results or ())}
    samples: dict[BenchmarkCase, list[float]] = {case: [] for case in cases}
    preparation_counts: dict[BenchmarkCase, int] = {}
    loop = asyncio.new_event_loop()
    contract_module = cast(Any, task_contracts)
    original_contract_datetime = contract_module.datetime
    contract_module.datetime = _FixedDateTime
    try:
        for case in cases:
            preparation_counts[case] = _measure_preparations(
                loop,
                fixtures[case],
                legacy_duplicate_preparation=legacy_duplicate_preparation,
            )
        for repeat_index in range(repeats):
            ordered = cases[repeat_index % len(cases) :] + cases[: repeat_index % len(cases)]
            if repeat_index % 2:
                ordered.reverse()
            for case in ordered:
                operation, _client, exchange = _operation_for(
                    fixtures[case],
                    legacy_duplicate_preparation=legacy_duplicate_preparation,
                )
                loop.run_until_complete(operation())
                before = exchange.published_count
                samples[case].append(_time_operation(loop, operation, case.iterations))
                expected = before + case.iterations * fixtures[case].publications_per_operation
                if exchange.published_count != expected:
                    raise RuntimeError(
                        f"{case.message_kind} publication count mismatch: "
                        f"expected {expected}, observed {exchange.published_count}."
                    )
    finally:
        contract_module.datetime = original_contract_datetime
        loop.close()

    results: list[BenchmarkResult] = []
    for case in cases:
        fixture = fixtures[case]
        case_samples = tuple(samples[case])
        median_ns = median(case_samples)
        mad_ns = median(tuple(abs(sample - median_ns) for sample in case_samples))
        total_operations = case.iterations * repeats
        baseline = baseline_by_identity.get(_case_identity(case))
        results.append(
            BenchmarkResult(
                case=case,
                actual_message_bytes=fixture.actual_message_bytes,
                bytes_per_operation=fixture.bytes_per_operation,
                publications_per_operation=fixture.publications_per_operation,
                preparations_per_operation=preparation_counts[case],
                repeats=repeats,
                sample_ns_per_operation=case_samples,
                median_ns_per_operation=median_ns,
                median_absolute_deviation_ns=mad_ns,
                operations_per_second=1_000_000_000.0 / median_ns,
                throughput_mib_per_second=(fixture.bytes_per_operation / (1024 * 1024) / (median_ns / 1_000_000_000.0)),
                total_operations=total_operations,
                total_prepared=preparation_counts[case] * total_operations,
                total_published=fixture.publications_per_operation * total_operations,
                relative_speedup=(baseline.median_ns_per_operation / median_ns if baseline is not None else None),
            )
        )
    return results


def _case_identity(case: BenchmarkCase) -> tuple[str, str, str, int]:
    return (case.message_kind, case.input_kind, case.topology, case.target_bytes)


def _result_data(result: BenchmarkResult) -> dict[str, Any]:
    return {
        "message_kind": result.case.message_kind,
        "input_kind": result.case.input_kind,
        "topology": result.case.topology,
        "target_label": result.case.target_label,
        "target_bytes": result.case.target_bytes,
        "iterations": result.case.iterations,
        "actual_message_bytes": result.actual_message_bytes,
        "bytes_per_operation": result.bytes_per_operation,
        "publications_per_operation": result.publications_per_operation,
        "preparations_per_operation": result.preparations_per_operation,
        "repeats": result.repeats,
        "sample_ns_per_operation": list(result.sample_ns_per_operation),
        "median_ns_per_operation": result.median_ns_per_operation,
        "median_absolute_deviation_ns": result.median_absolute_deviation_ns,
        "operations_per_second": result.operations_per_second,
        "throughput_mib_per_second": result.throughput_mib_per_second,
        "total_operations": result.total_operations,
        "total_prepared": result.total_prepared,
        "total_published": result.total_published,
        "relative_speedup": result.relative_speedup,
    }


def _result_from_data(data: Mapping[str, Any]) -> BenchmarkResult:
    case = BenchmarkCase(
        message_kind=cast(MessageKind, data["message_kind"]),
        input_kind=cast(InputKind, data["input_kind"]),
        topology=cast(TopologyKind, data["topology"]),
        target_label=str(data["target_label"]),
        target_bytes=int(data["target_bytes"]),
        iterations=int(data["iterations"]),
    )
    return BenchmarkResult(
        case=case,
        actual_message_bytes=int(data["actual_message_bytes"]),
        bytes_per_operation=int(data["bytes_per_operation"]),
        publications_per_operation=int(data["publications_per_operation"]),
        preparations_per_operation=int(data["preparations_per_operation"]),
        repeats=int(data["repeats"]),
        sample_ns_per_operation=tuple(float(value) for value in data["sample_ns_per_operation"]),
        median_ns_per_operation=float(data["median_ns_per_operation"]),
        median_absolute_deviation_ns=float(data["median_absolute_deviation_ns"]),
        operations_per_second=float(data["operations_per_second"]),
        throughput_mib_per_second=float(data["throughput_mib_per_second"]),
        total_operations=int(data["total_operations"]),
        total_prepared=int(data["total_prepared"]),
        total_published=int(data["total_published"]),
        relative_speedup=(None if data.get("relative_speedup") is None else float(data["relative_speedup"])),
    )


def load_embedded_results(report_path: Path) -> list[BenchmarkResult]:
    """Load self-contained comparison data from a prior HTML report."""

    content = report_path.read_text(encoding="utf-8")
    start = content.find(_EMBEDDED_DATA_PREFIX)
    end = content.find(_EMBEDDED_DATA_SUFFIX, start)
    if start < 0 or end < 0:
        raise ValueError(f"Report does not contain publish-preparation data: {report_path}")
    encoded = content[start + len(_EMBEDDED_DATA_PREFIX) : end].strip()
    payload = json.loads(html.unescape(encoded))
    return [_result_from_data(item) for item in payload["results"]]


def render_html(
    results: Sequence[BenchmarkResult],
    environment: Mapping[str, str],
    *,
    run_label: str,
    baseline_path: Path | None = None,
) -> str:
    """Render a human-readable self-contained benchmark report."""

    total_operations = sum(result.total_operations for result in results)
    total_prepared = sum(result.total_prepared for result in results)
    total_published = sum(result.total_published for result in results)
    individual = [result for result in results if result.case.message_kind == "individual-task"]
    preparation_passes = sorted({result.preparations_per_operation for result in individual})
    compared = [result for result in results if result.relative_speedup is not None]
    geometric_speedup = (
        _geometric_mean([cast(float, result.relative_speedup) for result in compared]) if compared else None
    )
    rows = "\n".join(
        f"""<tr>
          <td>{html.escape(result.case.message_kind)}</td>
          <td>{html.escape(result.case.input_kind)}</td>
          <td>{html.escape(result.case.topology)}</td>
          <td>{html.escape(result.case.target_label)}</td>
          <td class="number">{result.actual_message_bytes:,}</td>
          <td class="number">{result.case.iterations} × {result.repeats}</td>
          <td class="number">{result.preparations_per_operation}</td>
          <td class="number">{result.publications_per_operation}</td>
          <td class="number">{result.median_ns_per_operation / 1_000:.3f}</td>
          <td class="number">{result.median_absolute_deviation_ns / 1_000:.3f}</td>
          <td class="number">{result.operations_per_second:,.1f}</td>
          <td class="number">{result.throughput_mib_per_second:,.2f}</td>
          <td class="number">{"—" if result.relative_speedup is None else f"{result.relative_speedup:.3f}×"}</td>
        </tr>"""
        for result in results
    )
    metadata_rows = "\n".join(
        f"<tr><th>{html.escape(key)}</th><td>{html.escape(value)}</td></tr>" for key, value in environment.items()
    )
    embedded = html.escape(json.dumps({"results": [_result_data(result) for result in results]}))
    comparison_note = (
        "No baseline report was supplied; this report is a standalone measurement."
        if geometric_speedup is None
        else (
            f"Compared with {html.escape(str(baseline_path))}, the geometric mean total-path "
            f"speedup across {len(compared)} matched cells is <strong>{geometric_speedup:.3f}×</strong>."
        )
    )
    duplicate_note = (
        f"The untimed public-path probe observed {', '.join(map(str, preparation_passes))} "
        f"real task preparation call(s) per {TASKS_PER_OPERATION}-task individual operation. "
        f"Exactly {TASKS_PER_OPERATION} means one preparation per input task."
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Relayna Publish Preparation Benchmark — {html.escape(run_label)}</title>
  <style>
    :root {{ color-scheme: light; --ink:#172033; --muted:#566078; --panel:#f4f6fb; --line:#d8deea; --accent:#3659c9; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; color:var(--ink); background:#fff; font:15px/1.5 system-ui,-apple-system,sans-serif; }}
    main {{ width:min(1500px,96vw); margin:36px auto 72px; }}
    h1 {{ margin-bottom:4px; font-size:clamp(28px,4vw,44px); }}
    h2 {{ margin-top:34px; }}
    .lede {{ color:var(--muted); max-width:900px; }}
    .summary {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:10px; margin:24px 0; }}
    .summary div,.note {{ padding:14px 16px; background:var(--panel); border-left:4px solid var(--accent); }}
    .summary strong {{ display:block; font-size:22px; }}
    .table-wrap {{ overflow:auto; border:1px solid var(--line); }}
    table {{ border-collapse:collapse; width:100%; }}
    th,td {{ border-bottom:1px solid var(--line); padding:8px 10px; text-align:left; white-space:nowrap; }}
    thead th {{ position:sticky; top:0; background:#e9edf8; }}
    .number {{ text-align:right; font-variant-numeric:tabular-nums; }}
    code {{ background:#edf0f7; border-radius:4px; padding:2px 5px; }}
    @media print {{ main {{ width:100%; margin:0; }} thead th {{ position:static; }} }}
  </style>
</head>
<body>
<main>
  <h1>Relayna Publish Preparation Benchmark</h1>
  <p class="lede">Run label: <strong>{html.escape(run_label)}</strong>. Complete local CPU-side
  publishing from public input conversion through a deterministic no-op exchange boundary.</p>
  <div class="summary">
    <div><strong>{len(results)}</strong>matrix cells</div>
    <div><strong>{total_operations:,}</strong>timed operations</div>
    <div><strong>{total_prepared:,}</strong>task preparations</div>
    <div><strong>{total_published:,}</strong>fake publications</div>
  </div>
  <h2>Methodology</h2>
  <p>Deterministic fixtures use fixed identifiers and timestamps and are calibrated so each
  emitted AMQP body is exactly 1,024, 16,384, 131,072, or 1,048,576 bytes. Individual mode
  publishes two same-sized tasks per public operation with a concurrency limit of two;
  batch mode publishes one envelope containing two tasks. Model, canonical mapping, and
  configured-alias mapping inputs cover direct/shared task routing, task-type routing, and
  workflow-stage routing where applicable.</p>
  <p>Each timed operation includes alias normalization/input conversion, Pydantic validation
  and dumping, topology routing, trace/header construction, Pydantic Core transport JSON,
  <code>aio_pika.Message</code> construction, priority handling, metrics effects, and an
  async no-op exchange publish. It excludes connection setup, event-loop startup, sockets,
  broker work, and network latency. One event loop is reused for the entire run. Every cell
  receives an untimed warm-up; garbage collection is disabled only within a timed sample;
  case order rotates and reverses by repeat.</p>
  <p>Dispersion is median absolute deviation. Throughput uses binary MiB and counts all AMQP
  body bytes emitted per public operation. Allocation tracing is intentionally omitted from
  canonical latency samples. No timing threshold is an acceptance criterion.</p>
  <p class="note">{duplicate_note}</p>
  <h2>Before/after comparison</h2>
  <p>{comparison_note}</p>
  <h2>Results</h2>
  <div class="table-wrap"><table>
    <thead><tr>
      <th>Message kind</th><th>Input</th><th>Topology</th><th>Target</th>
      <th>Actual bytes/message</th><th>Iterations × repeats</th><th>Preparations/op</th>
      <th>Published/op</th><th>Median µs/op</th><th>MAD µs</th><th>Operations/sec</th>
      <th>MiB/sec</th><th>vs baseline</th>
    </tr></thead>
    <tbody>{rows}</tbody>
  </table></div>
  <h2>Actionable bottleneck conclusions</h2>
  <p>Compare input shapes within the same message kind/topology/size to isolate conversion
  and alias costs. Compare direct and routed task cases to expose routing overhead. Growth
  by payload size reflects JSON encoding and memory-copy cost. The public-path preparation
  count identifies redundant task validation independently of noisy timing.</p>
  <h2>Environment and reproducibility</h2>
  <table><tbody>{metadata_rows}</tbody></table>
</main>
{_EMBEDDED_DATA_PREFIX}{embedded}{_EMBEDDED_DATA_SUFFIX}
</body>
</html>
"""


def _geometric_mean(values: Sequence[float]) -> float:
    product = 1.0
    for value in values:
        product *= value
    return product ** (1.0 / len(values))


def write_html_report(
    output_path: Path,
    results: Sequence[BenchmarkResult],
    environment: Mapping[str, str],
    *,
    run_label: str,
    baseline_path: Path | None = None,
) -> Path:
    return write_text_artifact(
        output_path,
        render_html(results, environment, run_label=run_label, baseline_path=baseline_path),
    )


def _parse_iterations(values: Sequence[str] | None) -> dict[int, int]:
    iterations = dict(DEFAULT_ITERATIONS)
    for value in values or ():
        try:
            label, raw_count = value.split("=", 1)
            target_bytes = TARGET_SIZES[label.strip()]
            count = int(raw_count)
        except (KeyError, ValueError) as exc:
            raise argparse.ArgumentTypeError(
                f"Iterations must use one of {tuple(TARGET_SIZES)} as LABEL=COUNT."
            ) from exc
        if count < 1:
            raise argparse.ArgumentTypeError("Iteration counts must be positive.")
        iterations[target_bytes] = count
    return iterations


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
    parser.add_argument(
        "--iterations",
        action="append",
        metavar="LABEL=COUNT",
        help="Override iterations for a target size; repeat for multiple sizes.",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--run-label", default="candidate")
    parser.add_argument("--baseline-report", type=Path)
    parser.add_argument(
        "--legacy-duplicate-preparation",
        action="store_true",
        help="Reproduce the starting duplicate task-preparation paths for a fair retained baseline.",
    )


def run(args: argparse.Namespace) -> BenchmarkOutcome:
    if args.repeats < 1:
        raise ValueError("Repeats must be positive.")
    iterations = _parse_iterations(args.iterations)
    baseline_results = load_embedded_results(args.baseline_report) if args.baseline_report is not None else None
    results = run_benchmarks(
        repeats=args.repeats,
        iterations_by_size=iterations,
        baseline_results=baseline_results,
        legacy_duplicate_preparation=args.legacy_duplicate_preparation,
    )
    environment = collect_environment(
        package_names=("relayna", "pydantic", "pydantic-core", "aio-pika", "prometheus-client"),
        extra={
            "Benchmark": "publish-preparation",
            "Run label": args.run_label,
            "Task preparation implementation": (
                "starting duplicate-preparation baseline" if args.legacy_duplicate_preparation else "current runtime"
            ),
            "Event loop policy": type(asyncio.get_event_loop_policy()).__name__,
            "Tasks per individual/batch operation": str(TASKS_PER_OPERATION),
            "Network/broker": "excluded; deterministic no-op exchange",
        },
    )
    report = write_html_report(
        args.output,
        results,
        environment,
        run_label=args.run_label,
        baseline_path=args.baseline_report,
    )
    return BenchmarkOutcome(artifacts=(report,), measurement_count=len(results))


BENCHMARK = BenchmarkDefinition(
    name="publish-preparation",
    summary="Benchmark complete CPU-side AMQP publish preparation without network or broker latency.",
    default_output=DEFAULT_OUTPUT,
    add_arguments=add_arguments,
    run=run,
)


__all__ = [
    "BENCHMARK",
    "DEFAULT_OUTPUT",
    "NoOpExchange",
    "TARGET_SIZES",
    "BenchmarkCase",
    "BenchmarkFixture",
    "BenchmarkResult",
    "build_fixture",
    "build_matrix",
    "load_embedded_results",
    "render_html",
    "run_benchmarks",
    "write_html_report",
]
