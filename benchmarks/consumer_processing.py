"""Benchmark Relayna's inbound TaskConsumer processing after AMQP delivery."""

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

from benchmarks.registry import BenchmarkDefinition, BenchmarkOutcome
from benchmarks.reporting import collect_environment, write_text_artifact
from relayna._transport_json import encode_transport_json
from relayna.consumer import LifecycleStatusConfig, TaskConsumer, TaskContext
from relayna.contracts import ContractAliasConfig, TaskEnvelope
from relayna.topology import SharedTasksSharedStatusTopology

Measurement = Literal["per-message", "consumer-loop", "all"]
InputKind = Literal["canonical", "configured-alias"]
Profile = Literal["minimal", "observability-enabled"]
AsyncOperation = Callable[[], Awaitable[None]]

TARGET_SIZES: dict[str, int] = {
    "1 KB": 1_024,
    "16 KB": 16_384,
    "128 KB": 131_072,
    "1 MB": 1_048_576,
}
DEFAULT_ITERATIONS: dict[int, int] = {
    1_024: 1_500,
    16_384: 400,
    131_072: 60,
    1_048_576: 8,
}
DEFAULT_LOOP_MESSAGES: dict[int, int] = {
    1_024: 8_192,
    16_384: 2_048,
    131_072: 256,
    1_048_576: 64,
}
DEFAULT_REPEATS = 5
PREFETCH_VALUES = (1, 8, 32)
DEFAULT_OUTPUT = Path("reports/consumer-processing.html")
_FIXED_TIMESTAMP = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
_SOURCE_QUEUE = "benchmark.tasks.queue"
_ALIAS_CONFIG = ContractAliasConfig(field_aliases={"task_id": "attempt_id"})
_TRACE_HEADERS = {
    "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
    "tracestate": "vendor=benchmark",
    "x-relayna-retry-attempt": 0,
    "batch_id": "benchmark-batch-0001",
    "batch_index": 0,
    "batch_size": 1,
}
_EMBEDDED_DATA_PREFIX = "<!-- relayna-consumer-processing-data:"
_EMBEDDED_DATA_SUFFIX = ":end -->"


@dataclass(frozen=True)
class PerMessageCase:
    """One real `_handle_message()` timing cell."""

    profile: Profile
    input_kind: InputKind
    target_label: str
    target_bytes: int
    iterations: int


@dataclass(frozen=True)
class ConsumerLoopCase:
    """One public `run_forever()` timing cell."""

    profile: Profile
    target_label: str
    target_bytes: int
    prefetch: int
    message_count: int


@dataclass(frozen=True)
class MessageFixture:
    """Prepared already-delivered AMQP body and metadata."""

    body: bytes
    actual_message_bytes: int
    input_kind: InputKind


@dataclass(frozen=True)
class PerMessageResult:
    """Latency, throughput, and behavioral counts for one per-message cell."""

    case: PerMessageCase
    actual_message_bytes: int
    repeats: int
    sample_ns_per_message: tuple[float, ...]
    median_ns_per_message: float
    median_absolute_deviation_ns: float
    messages_per_second: float
    throughput_mib_per_second: float
    total_messages: int
    handler_count: int
    ack_count: int
    reject_count: int
    observation_count: int


@dataclass(frozen=True)
class ConsumerLoopResult:
    """Batch duration, throughput, concurrency, and counts for one loop cell."""

    case: ConsumerLoopCase
    actual_message_bytes: int
    total_bytes_per_sample: int
    repeats: int
    sample_duration_ns: tuple[int, ...]
    median_total_duration_ns: float
    median_ns_per_message: float
    messages_per_second: float
    throughput_mib_per_second: float
    cumulative_timed_duration_ns: int
    handler_count: int
    ack_count: int
    reject_count: int
    observation_count: int
    peak_concurrency: int


class DeliveredMessage:
    """Narrow fake for the aio-pika message attributes TaskConsumer reads."""

    def __init__(self, body: bytes, *, delivery_tag: int = 1) -> None:
        self.body = body
        self.correlation_id = f"benchmark-correlation-{delivery_tag:06d}"
        self.headers = dict(_TRACE_HEADERS)
        self.content_type = "application/json"
        self.delivery_tag = delivery_tag
        self.redelivered = False
        self.ack_count = 0
        self.reject_count = 0
        self.reject_requeue_values: list[bool] = []

    async def ack(self) -> None:
        self.ack_count += 1

    async def reject(self, *, requeue: bool) -> None:
        self.reject_count += 1
        self.reject_requeue_values.append(requeue)


class HandlerProbe:
    """No-op async application boundary with counts and loop concurrency."""

    def __init__(self, *, yield_once: bool) -> None:
        self.yield_once = yield_once
        self.calls = 0
        self.active = 0
        self.peak_active = 0

    async def __call__(self, task: TaskEnvelope, context: TaskContext) -> None:
        del task, context
        self.calls += 1
        self.active += 1
        self.peak_active = max(self.peak_active, self.active)
        try:
            if self.yield_once:
                await asyncio.sleep(0)
        finally:
            self.active -= 1


class ObservationProbe:
    """Deterministic in-memory observation sink that retains no events."""

    def __init__(self) -> None:
        self.count = 0

    async def __call__(self, event: object) -> None:
        del event
        self.count += 1


class PreloadedIterator:
    """Finite iterator that stops its consumer immediately after exhaustion."""

    def __init__(self, messages: Sequence[DeliveredMessage], stop_callback: Callable[[], None]) -> None:
        self._messages = messages
        self._index = 0
        self._stop_callback = stop_callback

    async def __aenter__(self) -> PreloadedIterator:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        del exc_type, exc, tb

    def __aiter__(self) -> PreloadedIterator:
        return self

    async def __anext__(self) -> DeliveredMessage:
        if self._index >= len(self._messages):
            self._stop_callback()
            raise StopAsyncIteration
        message = self._messages[self._index]
        self._index += 1
        return message


class PreloadedQueue:
    """Queue fake supporting the iterator options read by TaskConsumer."""

    def __init__(self, messages: Sequence[DeliveredMessage]) -> None:
        self.messages = messages
        self.stop_callback: Callable[[], None] | None = None
        self.iterator_calls: list[dict[str, Any]] = []

    def iterator(
        self,
        *,
        arguments: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> PreloadedIterator:
        self.iterator_calls.append({"arguments": arguments, "timeout": timeout})
        if self.stop_callback is None:
            raise RuntimeError("Consumer stop callback was not configured.")
        return PreloadedIterator(self.messages, self.stop_callback)


class PreloadedChannel:
    """Channel fake with faithful declaration and close signatures."""

    def __init__(self, queue: PreloadedQueue) -> None:
        self.queue = queue
        self.declare_queue_calls: list[dict[str, Any]] = []
        self.close_count = 0

    async def declare_queue(
        self,
        name: str,
        *,
        durable: bool,
        arguments: dict[str, Any] | None = None,
    ) -> PreloadedQueue:
        self.declare_queue_calls.append({"name": name, "durable": durable, "arguments": arguments})
        return self.queue

    async def close(self) -> None:
        self.close_count += 1


class FakeRabbitClient:
    """Already-initialized Rabbit boundary used by both measurement paths."""

    def __init__(self, channel: PreloadedChannel | None = None) -> None:
        self.topology = _topology()
        self.alias_config = _ALIAS_CONFIG
        self.metrics = None
        self.channel = channel
        self.ensure_tasks_queue_count = 0
        self.acquire_prefetch: list[int] = []

    async def ensure_tasks_queue(self) -> str:
        self.ensure_tasks_queue_count += 1
        return _SOURCE_QUEUE

    async def acquire_channel(self, prefetch: int = 200) -> PreloadedChannel:
        self.acquire_prefetch.append(prefetch)
        if self.channel is None:
            raise RuntimeError("No preloaded channel was configured.")
        return self.channel


def _topology() -> SharedTasksSharedStatusTopology:
    return SharedTasksSharedStatusTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="benchmark.tasks.exchange",
        tasks_queue=_SOURCE_QUEUE,
        tasks_routing_key="benchmark.task.request",
        status_exchange="benchmark.status.exchange",
        status_queue="benchmark.status.queue",
        prefetch_count=1,
    )


def build_per_message_matrix(
    iterations_by_size: Mapping[int, int] | None = None,
) -> list[PerMessageCase]:
    """Return canonical, alias, minimal, and observable per-message cells."""

    iteration_counts = _validated_size_counts(
        DEFAULT_ITERATIONS if iterations_by_size is None else iterations_by_size,
        description="iteration",
    )
    return [
        PerMessageCase(
            profile=profile,
            input_kind=input_kind,
            target_label=target_label,
            target_bytes=target_bytes,
            iterations=iteration_counts[target_bytes],
        )
        for profile in ("minimal", "observability-enabled")
        for input_kind in ("canonical", "configured-alias")
        for target_label, target_bytes in TARGET_SIZES.items()
    ]


def build_consumer_loop_matrix(
    message_counts_by_size: Mapping[int, int] | None = None,
) -> list[ConsumerLoopCase]:
    """Return the bounded canonical loop matrix for prefetch 1, 8, and 32."""

    message_counts = _validated_size_counts(
        DEFAULT_LOOP_MESSAGES if message_counts_by_size is None else message_counts_by_size,
        description="loop message",
    )
    return [
        ConsumerLoopCase(
            profile=profile,
            target_label=target_label,
            target_bytes=target_bytes,
            prefetch=prefetch,
            message_count=max(message_counts[target_bytes], prefetch),
        )
        for profile in ("minimal", "observability-enabled")
        for target_label, target_bytes in TARGET_SIZES.items()
        for prefetch in PREFETCH_VALUES
    ]


def _validated_size_counts(values: Mapping[int, int], *, description: str) -> dict[int, int]:
    counts = dict(values)
    missing_sizes = set(TARGET_SIZES.values()) - counts.keys()
    if missing_sizes:
        raise ValueError(f"Missing {description} counts for byte sizes: {sorted(missing_sizes)}")
    if any(counts[size] < 1 for size in TARGET_SIZES.values()):
        raise ValueError(f"{description.title()} counts must be positive.")
    return counts


def _payload(input_kind: InputKind, content: str) -> dict[str, Any]:
    payload = TaskEnvelope(
        task_id="benchmark-task-000001",
        payload={"benchmark": "consumer-processing", "content": content},
        created_at=_FIXED_TIMESTAMP,
        service="benchmark",
        task_type="benchmark.execute",
        correlation_id="benchmark-correlation-000001",
        priority=4,
    ).model_dump(mode="json", exclude_none=True)
    if input_kind == "configured-alias":
        payload["attempt_id"] = payload.pop("task_id")
    return payload


def build_fixture(input_kind: InputKind, target_bytes: int) -> MessageFixture:
    """Build transport JSON with exactly the requested actual body size."""

    empty_body = encode_transport_json(_payload(input_kind, ""))
    if len(empty_body) > target_bytes:
        raise ValueError(f"Target {target_bytes} bytes is too small for {input_kind}; minimum is {len(empty_body)}.")
    content = "x" * (target_bytes - len(empty_body))
    body = encode_transport_json(_payload(input_kind, content))
    if len(body) != target_bytes:
        raise RuntimeError(f"Fixture sizing failed for {input_kind}: expected {target_bytes}, produced {len(body)}.")
    return MessageFixture(body=body, actual_message_bytes=len(body), input_kind=input_kind)


def _consumer(
    rabbit: FakeRabbitClient,
    handler: HandlerProbe,
    observation_probe: ObservationProbe,
    *,
    profile: Profile,
    prefetch: int | None = None,
) -> TaskConsumer:
    observation_sink = observation_probe if profile == "observability-enabled" else None
    return TaskConsumer(
        rabbitmq=cast(Any, rabbit),
        handler=handler,
        consumer_name="consumer-processing-benchmark",
        prefetch=prefetch,
        consume_timeout_seconds=None,
        idle_retry_seconds=0,
        lifecycle_statuses=LifecycleStatusConfig(enabled=False),
        retry_policy=None,
        observation_sink=observation_sink,
        dlq_store=None,
        alias_config=_ALIAS_CONFIG,
        metrics=None,
        lease_store=None,
    )


async def _run_iterations(operation: AsyncOperation, iterations: int) -> None:
    for _ in range(iterations):
        await operation()


def _time_iterations(
    loop: asyncio.AbstractEventLoop,
    operation: AsyncOperation,
    iterations: int,
) -> float:
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


def calculate_per_message_result(
    case: PerMessageCase,
    fixture: MessageFixture,
    samples: Sequence[float],
    *,
    repeats: int,
    handler_count: int,
    ack_count: int,
    reject_count: int,
    observation_count: int,
) -> PerMessageResult:
    """Calculate per-message metrics independently of measurement execution."""

    if not samples or any(sample <= 0 for sample in samples):
        raise ValueError("Per-message timing samples must be positive.")
    median_ns = median(samples)
    mad_ns = median(abs(sample - median_ns) for sample in samples)
    return PerMessageResult(
        case=case,
        actual_message_bytes=fixture.actual_message_bytes,
        repeats=repeats,
        sample_ns_per_message=tuple(samples),
        median_ns_per_message=median_ns,
        median_absolute_deviation_ns=mad_ns,
        messages_per_second=1_000_000_000.0 / median_ns,
        throughput_mib_per_second=(fixture.actual_message_bytes / (1024 * 1024) / (median_ns / 1_000_000_000.0)),
        total_messages=case.iterations * repeats,
        handler_count=handler_count,
        ack_count=ack_count,
        reject_count=reject_count,
        observation_count=observation_count,
    )


def run_per_message_benchmarks(
    *,
    repeats: int = DEFAULT_REPEATS,
    iterations_by_size: Mapping[int, int] | None = None,
) -> list[PerMessageResult]:
    """Measure real `_handle_message()` cells with one reused event loop."""

    if repeats < 1:
        raise ValueError("Repeats must be positive.")
    cases = build_per_message_matrix(iterations_by_size)
    fixtures = {
        (input_kind, target_bytes): build_fixture(input_kind, target_bytes)
        for input_kind in ("canonical", "configured-alias")
        for target_bytes in TARGET_SIZES.values()
    }
    operations: dict[PerMessageCase, AsyncOperation] = {}
    messages: dict[PerMessageCase, DeliveredMessage] = {}
    handlers: dict[PerMessageCase, HandlerProbe] = {}
    observation_probes: dict[PerMessageCase, ObservationProbe] = {}
    warm_counts: dict[PerMessageCase, tuple[int, int, int, int]] = {}
    samples: dict[PerMessageCase, list[float]] = {case: [] for case in cases}
    loop = asyncio.new_event_loop()
    try:
        for case in cases:
            message = DeliveredMessage(fixtures[(case.input_kind, case.target_bytes)].body)
            handler = HandlerProbe(yield_once=False)
            observations = ObservationProbe()
            consumer = _consumer(FakeRabbitClient(), handler, observations, profile=case.profile)

            async def operation(
                active_consumer: TaskConsumer = consumer,
                active_message: DeliveredMessage = message,
            ) -> None:
                await active_consumer._handle_message(
                    active_message,
                    source_queue_name=_SOURCE_QUEUE,
                    retry_infrastructure=None,
                )

            operations[case] = operation
            messages[case] = message
            handlers[case] = handler
            observation_probes[case] = observations
            loop.run_until_complete(operation())
            warm_counts[case] = (
                handler.calls,
                message.ack_count,
                message.reject_count,
                observations.count,
            )

        for repeat_index in range(repeats):
            ordered = cases[repeat_index % len(cases) :] + cases[: repeat_index % len(cases)]
            if repeat_index % 2:
                ordered.reverse()
            for case in ordered:
                samples[case].append(
                    _time_iterations(
                        loop,
                        operations[case],
                        case.iterations,
                    )
                )
    finally:
        loop.close()

    results: list[PerMessageResult] = []
    for case in cases:
        warm_handler, warm_acks, warm_rejects, warm_observations = warm_counts[case]
        handler_count = handlers[case].calls - warm_handler
        ack_count = messages[case].ack_count - warm_acks
        reject_count = messages[case].reject_count - warm_rejects
        observation_count = observation_probes[case].count - warm_observations
        expected = case.iterations * repeats
        _validate_success_counts(
            label=f"per-message {case}",
            expected=expected,
            handlers=handler_count,
            acks=ack_count,
            rejects=reject_count,
        )
        results.append(
            calculate_per_message_result(
                case,
                fixtures[(case.input_kind, case.target_bytes)],
                samples[case],
                repeats=repeats,
                handler_count=handler_count,
                ack_count=ack_count,
                reject_count=reject_count,
                observation_count=observation_count,
            )
        )
    return results


@dataclass(frozen=True)
class _LoopSample:
    duration_ns: int
    handler_count: int
    ack_count: int
    reject_count: int
    observation_count: int
    peak_concurrency: int


def _run_loop_sample(
    loop: asyncio.AbstractEventLoop,
    case: ConsumerLoopCase,
    fixture: MessageFixture,
    *,
    message_count: int,
    timed: bool,
) -> _LoopSample:
    messages = [DeliveredMessage(fixture.body, delivery_tag=index + 1) for index in range(message_count)]
    queue = PreloadedQueue(messages)
    channel = PreloadedChannel(queue)
    rabbit = FakeRabbitClient(channel)
    handler = HandlerProbe(yield_once=True)
    observations = ObservationProbe()
    consumer = _consumer(
        rabbit,
        handler,
        observations,
        profile=case.profile,
        prefetch=case.prefetch,
    )
    queue.stop_callback = consumer.stop

    gc_was_enabled = gc.isenabled()
    if timed and gc_was_enabled:
        gc.disable()
    try:
        started_ns = time.perf_counter_ns() if timed else 0
        loop.run_until_complete(consumer.run_forever())
        duration_ns = time.perf_counter_ns() - started_ns if timed else 0
    finally:
        if timed and gc_was_enabled:
            gc.enable()

    ack_count = sum(message.ack_count for message in messages)
    reject_count = sum(message.reject_count for message in messages)
    _validate_success_counts(
        label=f"consumer-loop {case}",
        expected=message_count,
        handlers=handler.calls,
        acks=ack_count,
        rejects=reject_count,
    )
    if rabbit.ensure_tasks_queue_count != 1:
        raise RuntimeError(f"Consumer loop ensured its queue {rabbit.ensure_tasks_queue_count} times.")
    if rabbit.acquire_prefetch != [case.prefetch]:
        raise RuntimeError(f"Consumer loop expected prefetch {case.prefetch}, observed {rabbit.acquire_prefetch}.")
    if len(queue.iterator_calls) != 1 or channel.close_count != 1:
        raise RuntimeError("Consumer loop did not use exactly one iterator and channel lifecycle.")
    expected_peak = min(case.prefetch, message_count)
    if handler.peak_active != expected_peak:
        raise RuntimeError(f"Consumer loop expected peak concurrency {expected_peak}, observed {handler.peak_active}.")
    return _LoopSample(
        duration_ns=duration_ns,
        handler_count=handler.calls,
        ack_count=ack_count,
        reject_count=reject_count,
        observation_count=observations.count,
        peak_concurrency=handler.peak_active,
    )


def _validate_success_counts(
    *,
    label: str,
    expected: int,
    handlers: int,
    acks: int,
    rejects: int,
) -> None:
    if handlers != expected or acks != expected or rejects != 0:
        raise RuntimeError(
            f"{label} expected handler/ack/reject counts {expected}/{expected}/0, observed {handlers}/{acks}/{rejects}."
        )


def calculate_consumer_loop_result(
    case: ConsumerLoopCase,
    fixture: MessageFixture,
    samples: Sequence[_LoopSample],
    *,
    repeats: int,
) -> ConsumerLoopResult:
    """Calculate loop metrics independently of measurement execution."""

    durations = tuple(sample.duration_ns for sample in samples)
    if not durations or any(duration <= 0 for duration in durations):
        raise ValueError("Consumer-loop timing samples must be positive.")
    median_duration_ns = median(durations)
    median_ns_per_message = median_duration_ns / case.message_count
    return ConsumerLoopResult(
        case=case,
        actual_message_bytes=fixture.actual_message_bytes,
        total_bytes_per_sample=fixture.actual_message_bytes * case.message_count,
        repeats=repeats,
        sample_duration_ns=durations,
        median_total_duration_ns=median_duration_ns,
        median_ns_per_message=median_ns_per_message,
        messages_per_second=1_000_000_000.0 / median_ns_per_message,
        throughput_mib_per_second=(
            fixture.actual_message_bytes / (1024 * 1024) / (median_ns_per_message / 1_000_000_000.0)
        ),
        cumulative_timed_duration_ns=sum(durations),
        handler_count=sum(sample.handler_count for sample in samples),
        ack_count=sum(sample.ack_count for sample in samples),
        reject_count=sum(sample.reject_count for sample in samples),
        observation_count=sum(sample.observation_count for sample in samples),
        peak_concurrency=max(sample.peak_concurrency for sample in samples),
    )


def run_consumer_loop_benchmarks(
    *,
    repeats: int = DEFAULT_REPEATS,
    message_counts_by_size: Mapping[int, int] | None = None,
) -> list[ConsumerLoopResult]:
    """Measure public `run_forever()` over prepared deterministic queues."""

    if repeats < 1:
        raise ValueError("Repeats must be positive.")
    cases = build_consumer_loop_matrix(message_counts_by_size)
    fixtures = {target_bytes: build_fixture("canonical", target_bytes) for target_bytes in TARGET_SIZES.values()}
    samples: dict[ConsumerLoopCase, list[_LoopSample]] = {case: [] for case in cases}
    loop = asyncio.new_event_loop()
    try:
        for case in cases:
            _run_loop_sample(
                loop,
                case,
                fixtures[case.target_bytes],
                message_count=max(1, case.prefetch),
                timed=False,
            )

        for repeat_index in range(repeats):
            ordered = cases[repeat_index % len(cases) :] + cases[: repeat_index % len(cases)]
            if repeat_index % 2:
                ordered.reverse()
            for case in ordered:
                samples[case].append(
                    _run_loop_sample(
                        loop,
                        case,
                        fixtures[case.target_bytes],
                        message_count=case.message_count,
                        timed=True,
                    )
                )
    finally:
        loop.close()

    results: list[ConsumerLoopResult] = []
    for case in cases:
        result = calculate_consumer_loop_result(
            case,
            fixtures[case.target_bytes],
            samples[case],
            repeats=repeats,
        )
        expected = case.message_count * repeats
        _validate_success_counts(
            label=f"consumer-loop aggregate {case}",
            expected=expected,
            handlers=result.handler_count,
            acks=result.ack_count,
            rejects=result.reject_count,
        )
        results.append(result)
    return results


def _per_message_data(result: PerMessageResult) -> dict[str, Any]:
    return {
        "profile": result.case.profile,
        "input_kind": result.case.input_kind,
        "target_label": result.case.target_label,
        "target_bytes": result.case.target_bytes,
        "actual_message_bytes": result.actual_message_bytes,
        "iterations": result.case.iterations,
        "repeats": result.repeats,
        "sample_ns_per_message": result.sample_ns_per_message,
        "median_ns_per_message": result.median_ns_per_message,
        "median_absolute_deviation_ns": result.median_absolute_deviation_ns,
        "messages_per_second": result.messages_per_second,
        "throughput_mib_per_second": result.throughput_mib_per_second,
        "total_messages": result.total_messages,
        "handler_count": result.handler_count,
        "ack_count": result.ack_count,
        "reject_count": result.reject_count,
        "observation_count": result.observation_count,
    }


def _loop_data(result: ConsumerLoopResult) -> dict[str, Any]:
    return {
        "profile": result.case.profile,
        "input_kind": "canonical",
        "target_label": result.case.target_label,
        "target_bytes": result.case.target_bytes,
        "actual_message_bytes": result.actual_message_bytes,
        "prefetch": result.case.prefetch,
        "message_count": result.case.message_count,
        "total_bytes_per_sample": result.total_bytes_per_sample,
        "repeats": result.repeats,
        "sample_duration_ns": result.sample_duration_ns,
        "median_total_duration_ns": result.median_total_duration_ns,
        "median_ns_per_message": result.median_ns_per_message,
        "messages_per_second": result.messages_per_second,
        "throughput_mib_per_second": result.throughput_mib_per_second,
        "cumulative_timed_duration_ns": result.cumulative_timed_duration_ns,
        "handler_count": result.handler_count,
        "ack_count": result.ack_count,
        "reject_count": result.reject_count,
        "observation_count": result.observation_count,
        "peak_concurrency": result.peak_concurrency,
    }


def _ratio_summary(
    results: Sequence[PerMessageResult],
    *,
    numerator_profile: Profile | None = None,
    numerator_input: InputKind | None = None,
) -> float | None:
    ratios: list[float] = []
    by_key = {(result.case.profile, result.case.input_kind, result.case.target_bytes): result for result in results}
    for result in results:
        if numerator_profile is not None and result.case.profile == numerator_profile:
            baseline = by_key.get(("minimal", result.case.input_kind, result.case.target_bytes))
        elif numerator_input is not None and result.case.input_kind == numerator_input:
            baseline = by_key.get((result.case.profile, "canonical", result.case.target_bytes))
        else:
            continue
        if baseline is not None:
            ratios.append(result.median_ns_per_message / baseline.median_ns_per_message)
    return median(ratios) if ratios else None


def render_html(
    per_message_results: Sequence[PerMessageResult],
    loop_results: Sequence[ConsumerLoopResult],
    environment: Mapping[str, str],
    *,
    measurement: Measurement,
) -> str:
    """Render one self-contained report with distinct measurement sections."""

    per_rows = "\n".join(
        f"""<tr>
          <td>{html.escape(result.case.profile)}</td>
          <td>{html.escape(result.case.input_kind)}</td>
          <td>{html.escape(result.case.target_label)}</td>
          <td class="number">{result.actual_message_bytes:,}</td>
          <td class="number">{result.case.iterations} × {result.repeats}</td>
          <td class="number">{result.median_ns_per_message / 1_000:.3f}</td>
          <td class="number">{result.median_absolute_deviation_ns / 1_000:.3f}</td>
          <td class="number">{result.messages_per_second:,.1f}</td>
          <td class="number">{result.throughput_mib_per_second:,.2f}</td>
          <td class="number">{result.handler_count:,}</td>
          <td class="number">{result.ack_count:,}</td>
          <td class="number">{result.reject_count:,}</td>
          <td class="number">{result.observation_count:,}</td>
        </tr>"""
        for result in per_message_results
    )
    loop_rows = "\n".join(
        f"""<tr>
          <td>{html.escape(result.case.profile)}</td>
          <td>{html.escape(result.case.target_label)}</td>
          <td class="number">{result.actual_message_bytes:,}</td>
          <td class="number">{result.case.message_count:,}</td>
          <td class="number">{result.total_bytes_per_sample:,}</td>
          <td class="number">{result.case.prefetch}</td>
          <td class="number">{result.peak_concurrency}</td>
          <td class="number">{result.median_total_duration_ns / 1_000_000:.3f}</td>
          <td class="number">{result.cumulative_timed_duration_ns / 1_000_000:.3f}</td>
          <td class="number">{result.median_ns_per_message / 1_000:.3f}</td>
          <td class="number">{result.messages_per_second:,.1f}</td>
          <td class="number">{result.throughput_mib_per_second:,.2f}</td>
          <td class="number">{result.handler_count:,}</td>
          <td class="number">{result.ack_count:,}</td>
          <td class="number">{result.reject_count:,}</td>
          <td class="number">{result.observation_count:,}</td>
        </tr>"""
        for result in loop_results
    )
    per_content = (
        f"""<div class="table-wrap"><table>
        <thead><tr><th>Profile</th><th>Input</th><th>Target</th><th>Actual bytes</th>
        <th>Iterations × repeats</th><th>Median µs/message</th><th>MAD µs</th>
        <th>Messages/sec</th><th>MiB/sec</th><th>Handlers</th><th>Acks</th><th>Rejects</th>
        <th>Observations</th>
        </tr></thead><tbody>{per_rows}</tbody></table></div>"""
        if per_rows
        else '<p class="note">Not selected for this report run.</p>'
    )
    loop_content = (
        f"""<div class="table-wrap"><table>
        <thead><tr><th>Profile</th><th>Target</th><th>Actual bytes</th><th>Messages/sample</th>
        <th>Total bytes/sample</th><th>Prefetch</th><th>Peak concurrency</th>
        <th>Median total ms</th><th>Cumulative timed ms</th><th>µs/message</th>
        <th>Messages/sec</th><th>MiB/sec</th><th>Handlers</th><th>Acks</th><th>Rejects</th>
        <th>Observations</th>
        </tr></thead><tbody>{loop_rows}</tbody></table></div>"""
        if loop_rows
        else '<p class="note">Not selected for this report run.</p>'
    )
    metadata_rows = "\n".join(
        f"<tr><th>{html.escape(key)}</th><td>{html.escape(value)}</td></tr>" for key, value in environment.items()
    )
    alias_ratio = _ratio_summary(per_message_results, numerator_input="configured-alias")
    observation_ratio = _ratio_summary(
        per_message_results,
        numerator_profile="observability-enabled",
    )
    alias_text = "not measured" if alias_ratio is None else f"{alias_ratio:.3f}× canonical"
    observation_text = "not measured" if observation_ratio is None else f"{observation_ratio:.3f}× minimal"
    embedded = html.escape(
        json.dumps(
            {
                "measurement": measurement,
                "per_message_results": [_per_message_data(result) for result in per_message_results],
                "consumer_loop_results": [_loop_data(result) for result in loop_results],
            }
        )
    )
    total_cells = len(per_message_results) + len(loop_results)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Relayna Consumer Processing Benchmark</title>
  <style>
    :root {{ color-scheme:light; --ink:#172033; --muted:#566078; --panel:#f4f6fb;
      --line:#d8deea; --accent:#3659c9; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; color:var(--ink); background:#fff;
      font:15px/1.5 system-ui,-apple-system,sans-serif; }}
    main {{ width:min(1600px,96vw); margin:36px auto 72px; }}
    h1 {{ margin-bottom:4px; font-size:clamp(28px,4vw,44px); }}
    h2 {{ margin-top:34px; }}
    .lede {{ color:var(--muted); max-width:1000px; }}
    .summary {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr));
      gap:10px; margin:24px 0; }}
    .summary div,.note {{ padding:14px 16px; background:var(--panel);
      border-left:4px solid var(--accent); }}
    .summary strong {{ display:block; font-size:22px; }}
    .table-wrap {{ overflow:auto; border:1px solid var(--line); }}
    table {{ border-collapse:collapse; width:100%; }}
    th,td {{ border-bottom:1px solid var(--line); padding:8px 10px;
      text-align:left; white-space:nowrap; }}
    thead th {{ position:sticky; top:0; background:#e9edf8; }}
    .number {{ text-align:right; font-variant-numeric:tabular-nums; }}
    code {{ background:#edf0f7; border-radius:4px; padding:2px 5px; }}
    @media print {{ main {{ width:100%; margin:0; }} thead th {{ position:static; }} }}
  </style>
</head>
<body>
<main>
  <h1>Relayna Consumer Processing Benchmark</h1>
  <p class="lede">Real inbound <code>TaskConsumer</code> processing after RabbitMQ delivery,
  excluding broker/network latency and application business logic. Selected measurement:
  <strong>{html.escape(measurement)}</strong>.</p>
  <div class="summary">
    <div><strong>{total_cells}</strong>measurement cells</div>
    <div><strong>{len(per_message_results)}</strong>per-message cells</div>
    <div><strong>{len(loop_results)}</strong>consumer-loop cells</div>
    <div><strong>1 / 8 / 32</strong>loop prefetch values</div>
  </div>

  <h2>Methodology</h2>
  <p><strong>Per-message</strong> calls the real
  <code>TaskConsumer._handle_message()</code> operation once per sample operation. It includes
  Pydantic Core transport JSON parsing, configured alias normalization,
  <code>TaskEnvelope</code> validation, trace and header extraction, observation-event
  construction and optional sink calls, <code>TaskContext</code> construction, a no-op async
  handler invocation, resource samples, and the real acknowledgement decision. One event loop
  and prepared fixtures are reused; fixture construction and <code>asyncio.run()</code> are not
  timed. Dispersion is median absolute deviation.</p>
  <p><strong>Consumer-loop</strong> calls public <code>TaskConsumer.run_forever()</code> over a
  finite preloaded queue and channel. It includes iterator traversal, sequential or bounded
  dispatch, asyncio task creation/scheduling, prefetch selection, the same real message path,
  and acknowledgement. Each cell receives an untimed warm-up using separate fakes. Timed setup
  objects are prepared before the clock starts. Iterator exhaustion calls <code>stop()</code>
  synchronously, then in-flight tasks drain; there is no timeout polling or retry sleep.</p>
  <p>All bodies are actual exact sizes of 1,024, 16,384, 131,072, and 1,048,576
  bytes. Per-message covers canonical and configured-alias input. Loop timing uses canonical
  input only because alias parsing is already isolated in per-message results and repeating it
  at every prefetch would not add loop-dispatch information.</p>

  <h2>Matrix and configuration</h2>
  <p>The minimal profile has no observation sink. The observability-enabled profile uses a
  deterministic in-memory no-op sink and retains no events. Both profiles keep Prometheus
  metrics, lifecycle-status publishing, leases, retries, DLQ persistence, Redis, RabbitMQ
  connections/sockets, queue-declaration latency, and handler work off. Trace/header extraction
  and observation-event construction remain part of the real consumer path in both profiles.
  The loop's no-op handler yields once, adding no business computation while making achieved
  bounded scheduling concurrency directly observable.</p>

  <h2>Measurement 1 — per-message</h2>
  {per_content}

  <h2>Measurement 2 — consumer-loop</h2>
  {loop_content}

  <h2>Bottleneck conclusions</h2>
  <p>Across matched per-message cells, configured-alias latency is
  <strong>{alias_text}</strong>; in-memory observation delivery is
  <strong>{observation_text}</strong>. These small ratios can sit within run-to-run noise and
  should not be treated as causal phase attribution. Payload-size growth makes transport JSON
  parsing, normalization, envelope validation, and memory movement the likely large-message
  optimization targets. The gap between direct per-message and loop cost makes iterator/task
  scheduling the likely small-message loop target. Higher prefetch improves overlap for the
  yielding no-op handler but is not evidence that unbounded concurrency will help real handlers.
  Peak concurrency and exact handler/ack/reject counts provide behavioral evidence independently
  of timing noise.</p>
  <p class="note">Potential runtime optimizations are findings for a separate decision. This
  benchmark neither adds handler lookup or middleware traversal nor changes
  <code>src/relayna</code>.</p>

  <h2>Environment and package metadata</h2>
  <table><tbody>{metadata_rows}</tbody></table>
</main>
{_EMBEDDED_DATA_PREFIX}{embedded}{_EMBEDDED_DATA_SUFFIX}
</body>
</html>
"""


def write_html_report(
    output_path: Path,
    per_message_results: Sequence[PerMessageResult],
    loop_results: Sequence[ConsumerLoopResult],
    environment: Mapping[str, str],
    *,
    measurement: Measurement,
) -> Path:
    """Atomically write the self-contained consumer-processing report."""

    return write_text_artifact(
        output_path,
        render_html(
            per_message_results,
            loop_results,
            environment,
            measurement=measurement,
        ),
    )


def _parse_size_overrides(
    values: Sequence[str] | None,
    defaults: Mapping[int, int],
    *,
    noun: str,
) -> dict[int, int]:
    counts = dict(defaults)
    for value in values or ():
        try:
            label, raw_count = value.split("=", 1)
            target_bytes = TARGET_SIZES[label.strip()]
            count = int(raw_count)
        except (KeyError, ValueError) as exc:
            raise argparse.ArgumentTypeError(f"{noun} must use one of {tuple(TARGET_SIZES)} as LABEL=COUNT.") from exc
        if count < 1:
            raise argparse.ArgumentTypeError(f"{noun} counts must be positive.")
        counts[target_bytes] = count
    return counts


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--measurement",
        choices=("per-message", "consumer-loop", "all"),
        default="all",
        help="Select real per-message processing, the public consumer loop, or both (default: all).",
    )
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
    parser.add_argument(
        "--iterations",
        action="append",
        metavar="LABEL=COUNT",
        help="Override per-message iterations for a target size; repeat for multiple sizes.",
    )
    parser.add_argument(
        "--loop-messages",
        action="append",
        metavar="LABEL=COUNT",
        help=(
            "Override loop messages for a target size; counts are raised to at least prefetch "
            "to measure achieved concurrency."
        ),
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)


def run(args: argparse.Namespace) -> BenchmarkOutcome:
    """Dispatch selected measurements and write their shared HTML report."""

    if args.repeats < 1:
        raise ValueError("Repeats must be positive.")
    measurement = cast(Measurement, args.measurement)
    iterations = _parse_size_overrides(
        args.iterations,
        DEFAULT_ITERATIONS,
        noun="Iterations",
    )
    loop_messages = _parse_size_overrides(
        args.loop_messages,
        DEFAULT_LOOP_MESSAGES,
        noun="Loop messages",
    )
    per_message_results = (
        run_per_message_benchmarks(repeats=args.repeats, iterations_by_size=iterations)
        if measurement in {"per-message", "all"}
        else []
    )
    loop_results = (
        run_consumer_loop_benchmarks(
            repeats=args.repeats,
            message_counts_by_size=loop_messages,
        )
        if measurement in {"consumer-loop", "all"}
        else []
    )
    environment = collect_environment(
        package_names=(
            "relayna",
            "pydantic",
            "pydantic-core",
            "aio-pika",
            "opentelemetry-api",
            "prometheus-client",
        ),
        extra={
            "Benchmark": "consumer-processing",
            "Measurement": measurement,
            "Event loop policy": type(asyncio.get_event_loop_policy()).__name__,
            "Network/broker": "excluded; already-delivered messages and deterministic fakes",
            "Application handler": "async no-op; loop mode yields once for scheduling",
            "Input shapes": "canonical and configured alias per-message; canonical loop",
            "Profiles": "minimal and observability-enabled in-memory sink",
            "Optional features disabled": (
                "metrics, lifecycle statuses, leases, retries, DLQ, Redis, RabbitMQ network"
            ),
            "Exact body targets": ", ".join(f"{label}={target_bytes}" for label, target_bytes in TARGET_SIZES.items()),
            "Prefetch values": ", ".join(map(str, PREFETCH_VALUES)),
        },
    )
    report = write_html_report(
        args.output,
        per_message_results,
        loop_results,
        environment,
        measurement=measurement,
    )
    return BenchmarkOutcome(
        artifacts=(report,),
        measurement_count=len(per_message_results) + len(loop_results),
    )


BENCHMARK = BenchmarkDefinition(
    name="consumer-processing",
    summary="Benchmark real inbound TaskConsumer processing and its public loop without broker latency.",
    default_output=DEFAULT_OUTPUT,
    add_arguments=add_arguments,
    run=run,
)


__all__ = [
    "BENCHMARK",
    "DEFAULT_ITERATIONS",
    "DEFAULT_LOOP_MESSAGES",
    "DEFAULT_OUTPUT",
    "PREFETCH_VALUES",
    "TARGET_SIZES",
    "ConsumerLoopCase",
    "ConsumerLoopResult",
    "DeliveredMessage",
    "FakeRabbitClient",
    "HandlerProbe",
    "MessageFixture",
    "ObservationProbe",
    "PerMessageCase",
    "PerMessageResult",
    "PreloadedChannel",
    "PreloadedIterator",
    "PreloadedQueue",
    "build_consumer_loop_matrix",
    "build_fixture",
    "build_per_message_matrix",
    "calculate_consumer_loop_result",
    "calculate_per_message_result",
    "render_html",
    "run_consumer_loop_benchmarks",
    "run_per_message_benchmarks",
    "write_html_report",
]
