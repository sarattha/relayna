from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, ClassVar, Literal, cast

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from relayna._async import map_bounded
from relayna.contracts import BatchTaskEnvelope, ContractAliasConfig, TaskEnvelope
from relayna.metrics import RelaynaMetrics
from relayna.rabbitmq.client import RelaynaRabbitClient
from relayna.topology import RoutedTasksSharedStatusTopology, SharedTasksSharedStatusTopology

_FIXED_CREATED_AT = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
_ALIASES = ContractAliasConfig(field_aliases={"task_id": "attempt_id"})


class CountingTaskInput(BaseModel):
    model_config = ConfigDict(extra="allow")
    dump_calls: ClassVar[int] = 0

    task_id: str
    payload: dict[str, Any]
    created_at: datetime
    task_type: str
    correlation_id: str
    priority: int
    trace_id: str
    span_id: str

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        type(self).dump_calls += 1
        return super().model_dump(*args, **kwargs)


class CapturingExchange:
    def __init__(self, *, pause: bool = False) -> None:
        self.calls: list[tuple[Any, str]] = []
        self.active = 0
        self.peak_active = 0
        self.pause = pause

    async def publish(self, message: Any, *, routing_key: str) -> None:
        self.active += 1
        self.peak_active = max(self.peak_active, self.active)
        try:
            if self.pause:
                await asyncio.sleep(0)
            self.calls.append((message, routing_key))
        finally:
            self.active -= 1


class LegacyIndividualClient(RelaynaRabbitClient):
    """Starting-branch individual-mode implementation retained for equivalence tests."""

    async def publish_tasks(
        self,
        tasks: Sequence[BaseModel | Mapping[str, Any]],
        *,
        mode: Literal["individual", "batch_envelope"] = "individual",
        batch_id: str | None = None,
        meta: Mapping[str, Any] | None = None,
        max_concurrency: int = 16,
    ) -> None:
        if mode != "individual":
            await super().publish_tasks(
                tasks,
                mode=mode,
                batch_id=batch_id,
                meta=meta,
                max_concurrency=max_concurrency,
            )
            return
        prepared_tasks = [self._prepare_task_payload(task) for task in tasks]
        await map_bounded(prepared_tasks, self.publish_task, concurrency=max_concurrency)


class LegacyBatchClient(RelaynaRabbitClient):
    """Starting-branch batch task revalidation retained for equivalence tests."""

    async def publish_tasks(
        self,
        tasks: Sequence[BaseModel | Mapping[str, Any]],
        *,
        mode: Literal["individual", "batch_envelope"] = "individual",
        batch_id: str | None = None,
        meta: Mapping[str, Any] | None = None,
        max_concurrency: int = 16,
    ) -> None:
        if mode != "batch_envelope":
            await super().publish_tasks(
                tasks,
                mode=mode,
                batch_id=batch_id,
                meta=meta,
                max_concurrency=max_concurrency,
            )
            return
        prepared_tasks = [self._prepare_task_payload(task) for task in tasks]
        if not batch_id or not str(batch_id).strip():
            raise ValueError("batch_id is required when mode='batch_envelope'.")
        envelope = FixedBatchTaskEnvelope(
            batch_id=str(batch_id),
            tasks=[TaskEnvelope.model_validate(task) for task in prepared_tasks],
            meta=dict(meta or {}),
        )
        await self._publish_batch_envelope(
            envelope.model_dump(mode="json", exclude_none=True),
            priority=self._resolve_batch_priority(prepared_tasks),
        )


class FixedBatchTaskEnvelope(BatchTaskEnvelope):
    created_at: datetime = Field(default_factory=lambda: _FIXED_CREATED_AT)


def _topology(kind: Literal["direct", "routed"]) -> object:
    common = {
        "rabbitmq_url": "amqp://guest:guest@localhost:5672/",
        "tasks_exchange": "tasks.exchange",
        "tasks_queue": "tasks.queue",
        "status_exchange": "status.exchange",
        "status_queue": "status.queue",
        "task_max_priority": 8,
    }
    if kind == "direct":
        return SharedTasksSharedStatusTopology(**common, tasks_routing_key="task.request")
    return RoutedTasksSharedStatusTopology(**common, task_types=("task.review",))


def _canonical_task(sequence: int) -> dict[str, Any]:
    return {
        "task_id": f"task-{sequence}",
        "payload": {"sequence": sequence, "content": "payload"},
        "created_at": _FIXED_CREATED_AT.isoformat().replace("+00:00", "Z"),
        "task_type": "task.review",
        "correlation_id": f"correlation-{sequence}",
        "priority": 4,
        "trace_id": "1" * 32,
        "span_id": "2" * 16,
    }


def _input(kind: str, sequence: int) -> BaseModel | Mapping[str, Any]:
    canonical = _canonical_task(sequence)
    if kind == "model":
        return CountingTaskInput.model_validate(canonical)
    if kind == "alias-mapping":
        return {
            "attempt_id": canonical["task_id"],
            **{key: value for key, value in canonical.items() if key != "task_id"},
        }
    return canonical


def _client(
    client_type: type[RelaynaRabbitClient] = RelaynaRabbitClient,
    *,
    topology_kind: Literal["direct", "routed"] = "direct",
    pause: bool = False,
) -> tuple[RelaynaRabbitClient, CapturingExchange]:
    exchange = CapturingExchange(pause=pause)
    client = client_type(
        cast(Any, _topology(topology_kind)),
        alias_config=_ALIASES,
        metrics=RelaynaMetrics(service="preparation-regression"),
    )
    client._initialized = True
    client._tasks_exchange = cast(Any, exchange)
    return client, exchange


def _spy_task_validation(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    calls: list[object] = []
    original = TaskEnvelope.model_validate

    def counting_validate(cls: type[TaskEnvelope], value: object, *args: Any, **kwargs: Any) -> TaskEnvelope:
        calls.append(value)
        return original(value, *args, **kwargs)

    monkeypatch.setattr(TaskEnvelope, "model_validate", classmethod(counting_validate))
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize("input_kind", ["model", "canonical-mapping", "alias-mapping"])
async def test_publish_task_performs_one_real_validation_and_input_dump(
    monkeypatch: pytest.MonkeyPatch,
    input_kind: str,
) -> None:
    validation_calls = _spy_task_validation(monkeypatch)
    CountingTaskInput.dump_calls = 0
    client, exchange = _client()

    await client.publish_task(_input(input_kind, 1))

    assert len(validation_calls) == 1
    assert CountingTaskInput.dump_calls == (1 if input_kind == "model" else 0)
    assert len(exchange.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("input_kind", ["model", "canonical-mapping", "alias-mapping"])
async def test_publish_tasks_individual_prepares_each_input_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
    input_kind: str,
) -> None:
    validation_calls = _spy_task_validation(monkeypatch)
    CountingTaskInput.dump_calls = 0
    client, exchange = _client()
    tasks = [_input(input_kind, 1), _input(input_kind, 2)]

    await client.publish_tasks(tasks, mode="individual", max_concurrency=2)

    assert len(validation_calls) == len(tasks)
    assert CountingTaskInput.dump_calls == (len(tasks) if input_kind == "model" else 0)
    assert len(exchange.calls) == len(tasks)


@pytest.mark.asyncio
@pytest.mark.parametrize("input_kind", ["model", "canonical-mapping", "alias-mapping"])
async def test_publish_tasks_batch_validates_each_task_once(
    monkeypatch: pytest.MonkeyPatch,
    input_kind: str,
) -> None:
    validation_calls = _spy_task_validation(monkeypatch)
    CountingTaskInput.dump_calls = 0
    client, exchange = _client()
    tasks = [_input(input_kind, 1), _input(input_kind, 2)]

    await client.publish_tasks(tasks, mode="batch_envelope", batch_id="batch-1")

    assert len(validation_calls) == len(tasks)
    assert CountingTaskInput.dump_calls == (len(tasks) if input_kind == "model" else 0)
    assert len(exchange.calls) == 1


def _snapshot(exchange: CapturingExchange) -> list[dict[str, Any]]:
    return [
        {
            "routing_key": routing_key,
            "body": message.body,
            "correlation_id": message.correlation_id,
            "priority": message.priority,
            "headers": dict(message.headers or {}),
            "content_type": message.content_type,
            "delivery_mode": message.delivery_mode,
        }
        for message, routing_key in exchange.calls
    ]


def _queue_publish_samples(metrics: RelaynaMetrics) -> list[bytes]:
    return sorted(line for line in metrics.render().splitlines() if line.startswith(b"relayna_queue_publish_total{"))


@pytest.mark.asyncio
@pytest.mark.parametrize("input_kind", ["model", "canonical-mapping", "alias-mapping"])
@pytest.mark.parametrize("topology_kind", ["direct", "routed"])
async def test_individual_deduplication_preserves_complete_publish_behavior(
    monkeypatch: pytest.MonkeyPatch,
    input_kind: str,
    topology_kind: Literal["direct", "routed"],
) -> None:
    def deterministic_headers(headers: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return {**dict(headers or {}), "traceparent": "00-" + ("1" * 32) + "-" + ("2" * 16) + "-01"}

    monkeypatch.setattr("relayna.rabbitmq.client.inject_trace_headers", deterministic_headers)
    legacy, legacy_exchange = _client(
        LegacyIndividualClient,
        topology_kind=topology_kind,
        pause=True,
    )
    candidate, candidate_exchange = _client(topology_kind=topology_kind, pause=True)
    tasks = [_input(input_kind, 1), _input(input_kind, 2), _input(input_kind, 3)]

    await legacy.publish_tasks(tasks, mode="individual", max_concurrency=2)
    await candidate.publish_tasks(tasks, mode="individual", max_concurrency=2)

    assert _snapshot(candidate_exchange) == _snapshot(legacy_exchange)
    assert [message.correlation_id for message, _ in candidate_exchange.calls] == [
        "correlation-1",
        "correlation-2",
        "correlation-3",
    ]
    assert all(message.priority == 4 for message, _ in candidate_exchange.calls)
    assert all(
        message.headers["task_id"] == f"task-{index}" for index, (message, _) in enumerate(candidate_exchange.calls, 1)
    )
    assert candidate_exchange.peak_active == legacy_exchange.peak_active == 2
    assert candidate.metrics is not None
    assert legacy.metrics is not None
    assert _queue_publish_samples(candidate.metrics) == _queue_publish_samples(legacy.metrics)


@pytest.mark.asyncio
async def test_individual_deduplication_preserves_invalid_input_exception_and_eager_validation() -> None:
    legacy, legacy_exchange = _client(LegacyIndividualClient)
    candidate, candidate_exchange = _client()
    tasks = [_canonical_task(1), {"payload": {"invalid": True}}, _canonical_task(3)]

    with pytest.raises(ValidationError) as legacy_error:
        await legacy.publish_tasks(tasks, mode="individual", max_concurrency=2)
    with pytest.raises(ValidationError) as candidate_error:
        await candidate.publish_tasks(tasks, mode="individual", max_concurrency=2)

    assert legacy_error.value.errors(include_url=False) == candidate_error.value.errors(include_url=False)
    assert legacy_exchange.calls == candidate_exchange.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("input_kind", ["model", "canonical-mapping", "alias-mapping"])
@pytest.mark.parametrize("topology_kind", ["direct", "routed"])
async def test_batch_deduplication_preserves_bytes_routing_headers_priority_and_metrics(
    monkeypatch: pytest.MonkeyPatch,
    input_kind: str,
    topology_kind: Literal["direct", "routed"],
) -> None:
    monkeypatch.setattr("relayna.rabbitmq.client.BatchTaskEnvelope", FixedBatchTaskEnvelope)
    legacy, legacy_exchange = _client(LegacyBatchClient, topology_kind=topology_kind)
    candidate, candidate_exchange = _client(topology_kind=topology_kind)
    tasks = [_input(input_kind, 1), _input(input_kind, 2)]

    await legacy.publish_tasks(tasks, mode="batch_envelope", batch_id="batch-1", meta={"source": "test"})
    await candidate.publish_tasks(
        tasks,
        mode="batch_envelope",
        batch_id="batch-1",
        meta={"source": "test"},
    )

    assert _snapshot(candidate_exchange) == _snapshot(legacy_exchange)
    assert candidate_exchange.calls[0][0].headers["batch_id"] == "batch-1"
    assert candidate_exchange.calls[0][0].headers["batch_size"] == 2
    assert candidate_exchange.calls[0][0].correlation_id == "batch-1"
    assert candidate_exchange.calls[0][0].priority == 4
    assert candidate.metrics is not None
    assert legacy.metrics is not None
    assert _queue_publish_samples(candidate.metrics) == _queue_publish_samples(legacy.metrics)


@pytest.mark.asyncio
async def test_publish_task_preserves_headers_trace_fields_priority_and_routing() -> None:
    client, exchange = _client(topology_kind="routed")

    await client.publish_task(_canonical_task(1), headers={"custom": "header"})

    snapshot = _snapshot(exchange)[0]
    assert snapshot["routing_key"] == "task.review"
    assert snapshot["correlation_id"] == "correlation-1"
    assert snapshot["priority"] == 4
    assert snapshot["headers"]["task_id"] == "task-1"
    assert snapshot["headers"]["custom"] == "header"
    assert b'"trace_id":"11111111111111111111111111111111"' in snapshot["body"]
    assert b'"span_id":"2222222222222222"' in snapshot["body"]
