from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import pytest

from relayna.consumer import (
    AggregationConsumer,
    AggregationWorkerRuntime,
    FailureAction,
    LifecycleStatusConfig,
    RetryPolicy,
    RetryStatusConfig,
    TaskConsumer,
    TaskContext,
    WorkflowConsumer,
    WorkflowContext,
)
from relayna.contracts import ActionSchema, PayloadSchema, StatusEventEnvelope, WorkflowEnvelope
from relayna.dlq import DLQRecord
from relayna.metrics import RelaynaMetrics
from relayna.observability import (
    AggregationHandlerFailed,
    ConsumerDeadLetterPublished,
    ConsumerDLQRecordPersistFailed,
    ConsumerRetryScheduled,
    TaskConsumerLoopError,
    TaskConsumerStarted,
    TaskHandlerFailed,
    TaskLifecycleStatusPublished,
    TaskMessageAcked,
    TaskMessageReceived,
    TaskMessageRejected,
    TaskResourceSampled,
    WorkflowMessagePublished,
    WorkflowMessageReceived,
    WorkflowStageAcked,
    WorkflowStageFailed,
    WorkflowStageStarted,
)
from relayna.rabbitmq import RetryInfrastructure
from relayna.topology import (
    RoutedTasksSharedStatusTopology,
    SharedStatusWorkflowTopology,
    SharedTasksSharedStatusShardedAggregationTopology,
    SharedTasksSharedStatusTopology,
    WorkflowEntryRoute,
    WorkflowStage,
)


class FakeMessage:
    def __init__(
        self,
        body: bytes,
        *,
        correlation_id: str | None = None,
        headers: dict[str, Any] | None = None,
        content_type: str | None = "application/json",
        delivery_tag: int | None = 1,
        redelivered: bool = False,
    ) -> None:
        self.body = body
        self.correlation_id = correlation_id
        self.headers = dict(headers or {})
        self.content_type = content_type
        self.delivery_tag = delivery_tag
        self.redelivered = redelivered
        self.acked = False
        self.rejected_with: bool | None = None
        self.done = asyncio.Event()
        self._on_done: Callable[[], None] | None = None

    async def ack(self) -> None:
        self.acked = True
        self.done.set()
        if self._on_done is not None:
            self._on_done()

    async def reject(self, *, requeue: bool) -> None:
        self.rejected_with = requeue
        self.done.set()
        if self._on_done is not None:
            self._on_done()


class FakeIterator:
    def __init__(self, messages: list[FakeMessage]) -> None:
        self._messages = messages

    async def __aenter__(self) -> FakeIterator:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    def __aiter__(self) -> FakeIterator:
        return self

    async def __anext__(self) -> FakeMessage:
        if self._messages:
            return self._messages.pop(0)
        raise TimeoutError


class FakeQueue:
    def __init__(self, messages: list[FakeMessage]) -> None:
        self._messages = messages
        self.iterator_calls: list[dict[str, Any]] = []

    def iterator(self, *, arguments: dict[str, Any] | None = None, timeout: float | None = None) -> FakeIterator:
        self.iterator_calls.append({"arguments": arguments, "timeout": timeout})
        return FakeIterator(self._messages)


class FakeChannel:
    def __init__(self, queue: FakeQueue) -> None:
        self.queue = queue
        self.declare_queue_calls: list[dict[str, Any]] = []
        self.close_calls = 0

    async def declare_queue(self, name: str, *, durable: bool, arguments: dict[str, Any] | None = None) -> FakeQueue:
        self.declare_queue_calls.append({"name": name, "durable": durable, "arguments": arguments})
        return self.queue

    async def close(self) -> None:
        self.close_calls += 1


class FakeRabbitClient:
    def __init__(
        self,
        *,
        topology: SharedTasksSharedStatusTopology,
        queue_name: str = "tasks.queue",
        acquire_results: list[FakeChannel | Exception],
    ) -> None:
        self.topology = topology
        self.queue_name = queue_name
        self.acquire_results = list(acquire_results)
        self._last_channel: FakeChannel | None = None
        self.ensure_tasks_queue_calls = 0
        self.initialize_calls = 0
        self.close_calls = 0
        self.acquire_channel_calls: list[int] = []
        self.published_statuses: list[dict[str, Any]] = []
        self.published_status_calls: list[tuple[str, dict[str, Any]]] = []
        self.aggregation_queue_calls: list[dict[str, Any]] = []
        self.retry_infrastructure_calls: list[dict[str, Any]] = []
        self.raw_queue_publishes: list[dict[str, Any]] = []
        self.published_tasks: list[dict[str, Any]] = []
        self.workflow_queue_calls: list[str] = []
        self.published_workflows: list[dict[str, Any]] = []
        self.published_entries: list[dict[str, Any]] = []
        self.published_stage_messages: list[dict[str, Any]] = []

    async def ensure_tasks_queue(self) -> str:
        self.ensure_tasks_queue_calls += 1
        return self.queue_name

    async def ensure_workflow_queue(self, stage: str) -> str:
        self.workflow_queue_calls.append(stage)
        if isinstance(self.topology, SharedStatusWorkflowTopology):
            return self.topology.workflow_queue_name(stage)
        return self.queue_name

    async def acquire_channel(self, prefetch: int = 200) -> FakeChannel:
        self.acquire_channel_calls.append(prefetch)
        if not self.acquire_results:
            if self._last_channel is None:
                raise RuntimeError("no more channels")
            return self._last_channel
        result = self.acquire_results.pop(0)
        if isinstance(result, Exception):
            raise result
        self._last_channel = result
        return result

    async def initialize(self) -> None:
        self.initialize_calls += 1

    async def close(self) -> None:
        self.close_calls += 1

    async def publish_status(self, event: StatusEventEnvelope | dict[str, Any]) -> None:
        if isinstance(event, StatusEventEnvelope):
            payload = event.model_dump(mode="json", exclude_none=True)
        else:
            payload = dict(event)
        self.published_statuses.append(payload)
        self.published_status_calls.append(("status", payload))

    async def publish_aggregation_status(self, event: StatusEventEnvelope | dict[str, Any]) -> None:
        if isinstance(event, StatusEventEnvelope):
            payload = event.model_dump(mode="json", exclude_none=True)
        else:
            payload = dict(event)
        self.published_statuses.append(payload)
        self.published_status_calls.append(("aggregation", payload))

    async def publish_task(
        self,
        task: dict[str, Any],
        *,
        headers: dict[str, Any] | None = None,
    ) -> None:
        self.published_tasks.append({"task": dict(task), "headers": dict(headers or {})})

    async def publish_workflow(
        self,
        payload: WorkflowEnvelope | dict[str, Any],
        *,
        headers: dict[str, Any] | None = None,
    ) -> None:
        if isinstance(payload, WorkflowEnvelope):
            body = payload.as_transport_dict()
        else:
            body = dict(payload)
        self.published_workflows.append({"payload": body, "headers": dict(headers or {})})

    async def publish_to_stage(
        self,
        payload: WorkflowEnvelope | dict[str, Any],
        *,
        stage: str,
        headers: dict[str, Any] | None = None,
    ) -> None:
        if isinstance(payload, WorkflowEnvelope):
            body = payload.as_transport_dict()
        else:
            body = dict(payload)
        self.published_stage_messages.append({"payload": body, "stage": stage, "headers": dict(headers or {})})

    async def publish_to_entry(
        self,
        payload: WorkflowEnvelope | dict[str, Any],
        *,
        route: str,
        headers: dict[str, Any] | None = None,
    ) -> None:
        if isinstance(payload, WorkflowEnvelope):
            body = payload.as_transport_dict()
        else:
            body = dict(payload)
        self.published_entries.append({"payload": body, "route": route, "headers": dict(headers or {})})

    async def publish_workflow_message(
        self,
        payload: WorkflowEnvelope | dict[str, Any],
        *,
        routing_key: str,
        headers: dict[str, Any] | None = None,
    ) -> None:
        if isinstance(payload, WorkflowEnvelope):
            body = payload.as_transport_dict()
        else:
            body = dict(payload)
        self.published_workflows.append({"payload": body, "routing_key": routing_key, "headers": dict(headers or {})})

    async def ensure_aggregation_queue(self, *, shards: list[int], queue_name: str | None = None) -> str:
        self.aggregation_queue_calls.append({"shards": list(shards), "queue_name": queue_name})
        return queue_name or f"aggregation.queue.{'-'.join(str(shard) for shard in shards)}"

    async def ensure_retry_infrastructure(
        self,
        *,
        source_queue_name: str,
        delay_ms: int,
        retry_queue_suffix: str = ".retry",
        dead_letter_queue_suffix: str = ".dlq",
    ) -> RetryInfrastructure:
        self.retry_infrastructure_calls.append(
            {
                "source_queue_name": source_queue_name,
                "delay_ms": delay_ms,
                "retry_queue_suffix": retry_queue_suffix,
                "dead_letter_queue_suffix": dead_letter_queue_suffix,
            }
        )
        return RetryInfrastructure(
            source_queue_name=source_queue_name,
            retry_queue_name=f"{source_queue_name}{retry_queue_suffix}",
            dead_letter_queue_name=f"{source_queue_name}{dead_letter_queue_suffix}",
        )

    async def publish_raw_to_queue(
        self,
        queue_name: str,
        body: bytes,
        *,
        correlation_id: str | None = None,
        headers: dict[str, Any] | None = None,
        content_type: str | None = "application/json",
        delivery_mode: object | None = None,
    ) -> None:
        self.raw_queue_publishes.append(
            {
                "queue_name": queue_name,
                "body": body,
                "correlation_id": correlation_id,
                "headers": dict(headers or {}),
                "content_type": content_type,
                "delivery_mode": delivery_mode,
            }
        )


class FakeDLQStore:
    def __init__(self, *, fail: bool = False) -> None:
        self.records: list[DLQRecord] = []
        self.fail = fail

    async def add(self, record: DLQRecord) -> None:
        if self.fail:
            raise RuntimeError("dlq store unavailable")
        self.records.append(record)


class FakeContractStore:
    def __init__(
        self,
        *,
        acquire_result: bool = True,
        fail_mark_inflight: bool = False,
        fail_clear_inflight: bool = False,
        fail_release_dedup: bool = False,
    ) -> None:
        self.acquire_result = acquire_result
        self.fail_mark_inflight = fail_mark_inflight
        self.fail_clear_inflight = fail_clear_inflight
        self.fail_release_dedup = fail_release_dedup
        self.acquire_calls: list[dict[str, Any]] = []
        self.release_calls: list[dict[str, Any]] = []
        self.inflight_mark_calls: list[dict[str, Any]] = []
        self.inflight_clear_calls: list[dict[str, Any]] = []

    async def acquire_dedup(
        self,
        *,
        stage: str,
        task_id: str,
        action: str | None,
        payload: dict[str, Any],
        dedup_key_fields: tuple[str, ...],
    ) -> bool:
        self.acquire_calls.append(
            {
                "stage": stage,
                "task_id": task_id,
                "action": action,
                "payload": dict(payload),
                "dedup_key_fields": tuple(dedup_key_fields),
            }
        )
        return self.acquire_result

    async def release_dedup(
        self,
        *,
        stage: str,
        task_id: str,
        action: str | None,
        payload: dict[str, Any],
        dedup_key_fields: tuple[str, ...],
    ) -> None:
        if self.fail_release_dedup:
            raise RuntimeError("release unavailable")
        self.release_calls.append({"stage": stage, "task_id": task_id, "action": action})

    async def mark_inflight(
        self,
        *,
        stage: str,
        task_id: str,
        action: str | None,
        payload: dict[str, Any],
        dedup_key_fields: tuple[str, ...],
    ) -> None:
        if self.fail_mark_inflight:
            raise RuntimeError("mark unavailable")
        self.inflight_mark_calls.append({"stage": stage, "task_id": task_id, "action": action})

    async def clear_inflight(
        self,
        *,
        stage: str,
        task_id: str,
        action: str | None,
        payload: dict[str, Any],
        dedup_key_fields: tuple[str, ...],
    ) -> None:
        if self.fail_clear_inflight:
            raise RuntimeError("clear unavailable")
        self.inflight_clear_calls.append({"stage": stage, "task_id": task_id, "action": action})


def make_topology() -> SharedTasksSharedStatusTopology:
    return SharedTasksSharedStatusTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.queue",
        tasks_routing_key="task.request",
        status_exchange="status.exchange",
        status_queue="status.queue",
    )


def make_workflow_topology() -> SharedStatusWorkflowTopology:
    return SharedStatusWorkflowTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        workflow_exchange="workflow.exchange",
        status_exchange="status.exchange",
        status_queue="status.queue",
        stages=(
            WorkflowStage(
                name="topic_planner",
                queue="cq.topic_planner.in_queue",
                binding_keys=("planner.topic_planner.in",),
                publish_routing_key="planner.topic_planner.in",
            ),
            WorkflowStage(
                name="docsearch_planner",
                queue="cq.docsearch_planner.in_queue",
                binding_keys=("planner.docsearch_planner.in", "replanner.docsearch_planner.in"),
                publish_routing_key="planner.docsearch_planner.in",
            ),
        ),
        entry_routes=(
            WorkflowEntryRoute(
                name="planner",
                routing_key="planner.topic_planner.in",
                target_stage="topic_planner",
            ),
        ),
    )


async def run_consumer_until_message_done(
    consumer: TaskConsumer | AggregationConsumer | WorkflowConsumer,
    message: FakeMessage,
) -> None:
    previous_on_done = message._on_done
    message._on_done = consumer.stop
    try:
        task = asyncio.create_task(consumer.run_forever())
        await message.done.wait()
        consumer.stop()
        await task
    finally:
        message._on_done = previous_on_done


@pytest.mark.asyncio
async def test_task_context_publish_status_uses_task_and_correlation_id() -> None:
    rabbit = FakeRabbitClient(topology=make_topology(), acquire_results=[])
    context = TaskContext(
        rabbitmq=rabbit,
        consumer_name="worker-a",
        raw_payload={"task_id": "task-123"},
        correlation_id="corr-123",
        delivery_tag=7,
        redelivered=False,
        _task_id="task-123",
    )

    await context.publish_status(status="processing", message="Started.")
    await context.publish_status(StatusEventEnvelope(task_id="ignored", status="completed"))

    assert rabbit.published_statuses == [
        {
            "task_id": "task-123",
            "status": "processing",
            "message": "Started.",
            "meta": {},
            "correlation_id": "corr-123",
            "spec_version": "1.0",
            "timestamp": rabbit.published_statuses[0]["timestamp"],
        },
        {
            "task_id": "task-123",
            "status": "completed",
            "meta": {},
            "correlation_id": "corr-123",
            "spec_version": "1.0",
            "timestamp": rabbit.published_statuses[1]["timestamp"],
        },
    ]


@pytest.mark.asyncio
async def test_task_context_publish_aggregation_status_uses_parent_metadata() -> None:
    rabbit = FakeRabbitClient(topology=make_topology(), acquire_results=[])
    context = TaskContext(
        rabbitmq=rabbit,
        consumer_name="worker-a",
        raw_payload={"task_id": "task-123"},
        correlation_id="corr-123",
        delivery_tag=7,
        redelivered=False,
        _task_id="task-123",
    )

    await context.publish_aggregation_status(status="aggregating", meta={"parent_task_id": "parent-1"})

    assert rabbit.published_statuses[0]["status"] == "aggregating"
    assert rabbit.published_statuses[0]["meta"]["parent_task_id"] == "parent-1"


@pytest.mark.asyncio
async def test_workflow_context_publish_to_stage_preserves_task_identity_and_emits_observation() -> None:
    rabbit = FakeRabbitClient(topology=make_workflow_topology(), acquire_results=[])
    observations: list[object] = []

    async def sink(event: object) -> None:
        observations.append(event)

    context = WorkflowContext(
        rabbitmq=rabbit,
        consumer_name="workflow-a",
        stage="topic_planner",
        raw_payload={"task_id": "task-123", "message_id": "msg-1"},
        correlation_id="corr-123",
        delivery_tag=7,
        redelivered=False,
        _task_id="task-123",
        _message_id="msg-1",
        source_queue_name="cq.topic_planner.in_queue",
        observation_sink=sink,
    )

    await context.publish_to_stage("docsearch_planner", {"docs": ["a"]}, action="collect", priority=3)

    assert rabbit.published_stage_messages[0]["stage"] == "docsearch_planner"
    payload = rabbit.published_stage_messages[0]["payload"]
    assert payload["task_id"] == "task-123"
    assert payload["correlation_id"] == "corr-123"
    assert payload["origin_stage"] == "topic_planner"
    assert payload["stage"] == "docsearch_planner"
    assert payload["action"] == "collect"
    assert payload["priority"] == 3
    assert isinstance(observations[0], WorkflowMessagePublished)


@pytest.mark.asyncio
async def test_workflow_context_publish_workflow_message_resolves_stage_from_routing_key() -> None:
    rabbit = FakeRabbitClient(topology=make_workflow_topology(), acquire_results=[])
    context = WorkflowContext(
        rabbitmq=rabbit,
        consumer_name="workflow-a",
        stage="topic_planner",
        raw_payload={"task_id": "task-123", "message_id": "msg-1"},
        correlation_id="corr-123",
        delivery_tag=7,
        redelivered=False,
        _task_id="task-123",
        _message_id="msg-1",
    )

    await context.publish_workflow_message(
        "planner.docsearch_planner.in",
        {"docs": ["a"]},
        action="collect",
        priority=5,
    )

    payload = rabbit.published_workflows[0]["payload"]
    assert rabbit.published_workflows[0]["routing_key"] == "planner.docsearch_planner.in"
    assert payload["stage"] == "docsearch_planner"
    assert payload["origin_stage"] == "topic_planner"
    assert payload["priority"] == 5


@pytest.mark.asyncio
async def test_workflow_context_publish_workflow_message_accepts_alternate_stage_binding_key() -> None:
    rabbit = FakeRabbitClient(topology=make_workflow_topology(), acquire_results=[])
    context = WorkflowContext(
        rabbitmq=rabbit,
        consumer_name="workflow-a",
        stage="topic_planner",
        raw_payload={"task_id": "task-123", "message_id": "msg-1"},
        correlation_id="corr-123",
        delivery_tag=7,
        redelivered=False,
        _task_id="task-123",
        _message_id="msg-1",
    )

    await context.publish_workflow_message(
        "replanner.docsearch_planner.in",
        {"docs": ["a"]},
        action="collect",
    )

    payload = rabbit.published_workflows[0]["payload"]
    assert rabbit.published_workflows[0]["routing_key"] == "replanner.docsearch_planner.in"
    assert payload["stage"] == "docsearch_planner"
    assert payload["origin_stage"] == "topic_planner"


@pytest.mark.asyncio
async def test_workflow_context_publish_workflow_message_accepts_wildcard_stage_binding_key() -> None:
    topology = SharedStatusWorkflowTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        workflow_exchange="workflow.exchange",
        status_exchange="status.exchange",
        status_queue="status.queue",
        stages=(
            WorkflowStage(
                name="topic_planner",
                queue="cq.topic_planner.in_queue",
                binding_keys=("planner.topic_planner.in",),
                publish_routing_key="planner.topic_planner.in",
            ),
            WorkflowStage(
                name="docsearch_planner",
                queue="cq.docsearch_planner.in_queue",
                binding_keys=("planner.*.in",),
                publish_routing_key="planner.docsearch_planner.out",
            ),
        ),
    )
    rabbit = FakeRabbitClient(topology=topology, acquire_results=[])
    context = WorkflowContext(
        rabbitmq=rabbit,
        consumer_name="workflow-a",
        stage="topic_planner",
        raw_payload={"task_id": "task-123", "message_id": "msg-1"},
        correlation_id="corr-123",
        delivery_tag=7,
        redelivered=False,
        _task_id="task-123",
        _message_id="msg-1",
    )

    await context.publish_workflow_message(
        "planner.docsearch.in",
        {"docs": ["a"]},
        action="collect",
    )

    payload = rabbit.published_workflows[0]["payload"]
    assert rabbit.published_workflows[0]["routing_key"] == "planner.docsearch.in"
    assert payload["stage"] == "docsearch_planner"
    assert payload["origin_stage"] == "topic_planner"


@pytest.mark.asyncio
async def test_workflow_context_publish_workflow_message_prefers_exact_publish_key_over_wildcard_binding() -> None:
    topology = SharedStatusWorkflowTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        workflow_exchange="workflow.exchange",
        status_exchange="status.exchange",
        status_queue="status.queue",
        stages=(
            WorkflowStage(
                name="catch_all_planner",
                queue="cq.catch_all_planner.in_queue",
                binding_keys=("planner.*.in",),
                publish_routing_key="planner.catch_all_planner.in",
            ),
            WorkflowStage(
                name="docsearch_planner",
                queue="cq.docsearch_planner.in_queue",
                binding_keys=("planner.docsearch.in",),
                publish_routing_key="planner.docsearch.in",
            ),
        ),
    )
    rabbit = FakeRabbitClient(topology=topology, acquire_results=[])
    context = WorkflowContext(
        rabbitmq=rabbit,
        consumer_name="workflow-a",
        stage="catch_all_planner",
        raw_payload={"task_id": "task-123", "message_id": "msg-1"},
        correlation_id="corr-123",
        delivery_tag=7,
        redelivered=False,
        _task_id="task-123",
        _message_id="msg-1",
    )

    await context.publish_workflow_message(
        "planner.docsearch.in",
        {"docs": ["a"]},
        action="collect",
    )

    payload = rabbit.published_workflows[0]["payload"]
    assert payload["stage"] == "docsearch_planner"


@pytest.mark.asyncio
async def test_workflow_context_publish_workflow_message_prefers_entry_route_over_wildcard_binding() -> None:
    topology = SharedStatusWorkflowTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        workflow_exchange="workflow.exchange",
        status_exchange="status.exchange",
        status_queue="status.queue",
        stages=(
            WorkflowStage(
                name="catch_all_planner",
                queue="cq.catch_all_planner.in_queue",
                binding_keys=("planner.#",),
                publish_routing_key="planner.catch_all_planner.in",
            ),
            WorkflowStage(
                name="docsearch_planner",
                queue="cq.docsearch_planner.in_queue",
                binding_keys=("planner.docsearch.in",),
                publish_routing_key="planner.docsearch.in",
            ),
        ),
        entry_routes=(
            WorkflowEntryRoute(
                name="custom_docsearch",
                routing_key="planner.custom.in",
                target_stage="docsearch_planner",
            ),
        ),
    )
    rabbit = FakeRabbitClient(topology=topology, acquire_results=[])
    context = WorkflowContext(
        rabbitmq=rabbit,
        consumer_name="workflow-a",
        stage="catch_all_planner",
        raw_payload={"task_id": "task-123", "message_id": "msg-1"},
        correlation_id="corr-123",
        delivery_tag=7,
        redelivered=False,
        _task_id="task-123",
        _message_id="msg-1",
    )

    await context.publish_workflow_message(
        "planner.custom.in",
        {"docs": ["a"]},
        action="collect",
    )

    payload = rabbit.published_workflows[0]["payload"]
    assert payload["stage"] == "docsearch_planner"


@pytest.mark.asyncio
async def test_task_context_manual_retry_requires_task_envelope_context() -> None:
    rabbit = FakeRabbitClient(topology=make_topology(), acquire_results=[])
    context = TaskContext(
        rabbitmq=rabbit,
        consumer_name="worker-a",
        raw_payload={"task_id": "task-123", "status": "processing"},
        correlation_id="corr-123",
        delivery_tag=7,
        redelivered=False,
        _task_id="task-123",
        is_task_context=False,
    )

    with pytest.raises(ValueError, match="task envelope"):
        await context.manual_retry(task_type="task.review")


@pytest.mark.asyncio
async def test_task_consumer_manual_retry_republishes_task_and_acks_original_message() -> None:
    message = FakeMessage(
        json.dumps(
            {"task_id": "task-123", "task_type": "task.generate", "payload": {"quality": "low"}, "priority": 2}
        ).encode("utf-8"),
        correlation_id="corr-123",
        headers={"batch_id": "batch-123", "x-relayna-retry-attempt": 2},
    )
    queue = FakeQueue([message])
    rabbit = FakeRabbitClient(topology=make_topology(), acquire_results=[FakeChannel(queue)])

    async def handler(task: Any, context: TaskContext) -> None:
        await context.manual_retry(
            task_type="task.review",
            payload_merge={"quality": "high"},
            reason="quality gate failed",
            meta={"phase": "review"},
        )

    consumer = TaskConsumer(
        rabbitmq=rabbit,
        handler=handler,
        lifecycle_statuses=LifecycleStatusConfig(enabled=True),
    )

    await run_consumer_until_message_done(consumer, message)

    assert message.acked is True
    assert rabbit.published_tasks == [
        {
            "task": {
                "task_id": "task-123",
                "payload": {"quality": "high"},
                "task_type": "task.review",
                "correlation_id": "corr-123",
                "priority": 2,
                "spec_version": "1.0",
                "created_at": rabbit.published_tasks[0]["task"]["created_at"],
            },
            "headers": {
                "batch_id": "batch-123",
                "task_id": "task-123",
                "x-relayna-manual-retry-count": 1,
                "x-relayna-manual-retry-source-consumer": "relayna-task-consumer",
                "x-relayna-manual-retry-from-task-type": "task.generate",
                "x-relayna-manual-retry-reason": "quality gate failed",
            },
        }
    ]
    assert "x-relayna-retry-attempt" not in rabbit.published_tasks[0]["headers"]
    assert [event["status"] for event in rabbit.published_statuses] == ["processing", "manual_retrying"]
    assert rabbit.published_statuses[-1]["meta"]["phase"] == "review"
    assert rabbit.published_statuses[-1]["meta"]["manual_retry"]["target_task_type"] == "task.review"


@pytest.mark.asyncio
async def test_task_consumer_publishes_manual_retry_status_before_task_handoff() -> None:
    message = FakeMessage(
        json.dumps({"task_id": "task-123", "task_type": "task.generate", "payload": {"quality": "low"}}).encode(
            "utf-8"
        ),
        correlation_id="corr-123",
    )
    queue = FakeQueue([message])
    rabbit = FakeRabbitClient(topology=make_topology(), acquire_results=[FakeChannel(queue)])

    original_publish_task = rabbit.publish_task

    async def publish_task_with_assertion(task: dict[str, Any], *, headers: dict[str, Any] | None = None) -> None:
        assert [event["status"] for event in rabbit.published_statuses] == ["processing", "manual_retrying"]
        await original_publish_task(task, headers=headers)

    rabbit.publish_task = publish_task_with_assertion  # type: ignore[method-assign]

    async def handler(task: Any, context: TaskContext) -> None:
        await context.manual_retry(task_type="task.review")

    consumer = TaskConsumer(
        rabbitmq=rabbit,
        handler=handler,
        lifecycle_statuses=LifecycleStatusConfig(enabled=True),
    )

    await run_consumer_until_message_done(consumer, message)

    assert message.acked is True
    assert len(rabbit.published_tasks) == 1


@pytest.mark.asyncio
async def test_task_consumer_still_republishes_manual_retry_when_status_publish_fails() -> None:
    message = FakeMessage(
        json.dumps({"task_id": "task-123", "task_type": "task.generate", "payload": {"quality": "low"}}).encode(
            "utf-8"
        ),
        correlation_id="corr-123",
    )
    queue = FakeQueue([message])
    rabbit = FakeRabbitClient(topology=make_topology(), acquire_results=[FakeChannel(queue)])

    async def failing_publish_status(event: StatusEventEnvelope | dict[str, Any]) -> None:
        del event
        raise RuntimeError("status unavailable")

    rabbit.publish_status = failing_publish_status  # type: ignore[method-assign]

    async def handler(task: Any, context: TaskContext) -> None:
        await context.manual_retry(task_type="task.review")

    consumer = TaskConsumer(
        rabbitmq=rabbit,
        handler=handler,
        lifecycle_statuses=LifecycleStatusConfig(enabled=True),
    )

    await run_consumer_until_message_done(consumer, message)

    assert message.acked is True
    assert len(rabbit.published_tasks) == 1


@pytest.mark.asyncio
async def test_task_context_manual_retry_rejects_reserved_extra_fields() -> None:
    rabbit = FakeRabbitClient(topology=make_topology(), acquire_results=[])
    context = TaskContext(
        rabbitmq=rabbit,
        consumer_name="worker-a",
        raw_payload={"task_id": "task-123", "task_type": "task.generate", "payload": {"kind": "demo"}},
        correlation_id="corr-123",
        delivery_tag=1,
        redelivered=False,
        _task_id="task-123",
        headers={"task_id": "task-123"},
        is_task_context=True,
    )

    with pytest.raises(ValueError, match="reserved key"):
        await context.manual_retry(extra_fields={"priority": 9})


@pytest.mark.asyncio
async def test_task_context_manual_retry_overrides_priority_when_requested() -> None:
    rabbit = FakeRabbitClient(topology=make_topology(), acquire_results=[])
    context = TaskContext(
        rabbitmq=rabbit,
        consumer_name="worker-a",
        raw_payload={"task_id": "task-123", "task_type": "task.generate", "payload": {"kind": "demo"}, "priority": 2},
        correlation_id="corr-123",
        delivery_tag=1,
        redelivered=False,
        _task_id="task-123",
        headers={"task_id": "task-123"},
        is_task_context=True,
    )

    with pytest.raises(Exception) as exc_info:
        await context.manual_retry(task_type="task.review", priority=7)

    assert type(exc_info.value).__name__ == "_ManualRetryRequested"
    assert context._manual_retry_request is not None
    assert context._manual_retry_request.task["priority"] == 7


@pytest.mark.asyncio
async def test_task_context_propagates_manual_retry_lineage_to_status_updates() -> None:
    rabbit = FakeRabbitClient(topology=make_topology(), acquire_results=[])
    context = TaskContext(
        rabbitmq=rabbit,
        consumer_name="worker-b",
        raw_payload={"task_id": "task-123", "task_type": "task.review", "payload": {}},
        correlation_id="corr-123",
        delivery_tag=9,
        redelivered=False,
        _task_id="task-123",
        headers={
            "x-relayna-manual-retry-count": 2,
            "x-relayna-manual-retry-from-task-type": "task.generate",
            "x-relayna-manual-retry-source-consumer": "worker-a",
            "x-relayna-manual-retry-reason": "quality gate failed",
        },
        manual_retry_count=2,
        manual_retry_previous_task_type="task.generate",
        manual_retry_source_consumer="worker-a",
        manual_retry_reason="quality gate failed",
    )

    await context.publish_status(status="processing", meta={"stage": "review"})

    assert rabbit.published_statuses[0]["meta"]["stage"] == "review"
    assert rabbit.published_statuses[0]["meta"]["manual_retry"] == {
        "count": 2,
        "previous_task_type": "task.generate",
        "source_consumer": "worker-a",
        "reason": "quality gate failed",
    }


@pytest.mark.asyncio
async def test_task_consumer_acknowledges_successful_messages() -> None:
    message = FakeMessage(json.dumps({"task_id": "task-123", "payload": {"kind": "demo"}}).encode("utf-8"))
    queue = FakeQueue([message])
    channel = FakeChannel(queue)
    rabbit = FakeRabbitClient(topology=make_topology(), acquire_results=[channel])
    handled: list[str] = []

    async def handler(task: Any, context: TaskContext) -> None:
        handled.append(task.task_id)
        assert context.raw_payload["task_id"] == "task-123"

    consumer = TaskConsumer(rabbitmq=rabbit, handler=handler)

    await run_consumer_until_message_done(consumer, message)

    assert handled == ["task-123"]
    assert message.acked is True
    assert message.rejected_with is None
    assert rabbit.ensure_tasks_queue_calls == 1
    assert rabbit.acquire_channel_calls == [1]
    assert channel.declare_queue_calls == [{"name": "tasks.queue", "durable": True, "arguments": None}]
    assert channel.close_calls >= 1


@pytest.mark.asyncio
async def test_task_consumer_emits_resource_samples_and_runtime_metrics() -> None:
    message = FakeMessage(json.dumps({"task_id": "task-123", "task_type": "payment", "payload": {}}).encode("utf-8"))
    queue = FakeQueue([message])
    channel = FakeChannel(queue)
    rabbit = FakeRabbitClient(topology=make_topology(), acquire_results=[channel])
    observations: list[object] = []
    metrics = RelaynaMetrics(service="payments-api")

    async def sink(event: object) -> None:
        observations.append(event)

    async def handler(task: Any, context: TaskContext) -> None:
        del task, context

    consumer = TaskConsumer(rabbitmq=rabbit, handler=handler, observation_sink=sink, metrics=metrics)

    await run_consumer_until_message_done(consumer, message)

    samples = [item for item in observations if isinstance(item, TaskResourceSampled)]
    assert [sample.sample_kind for sample in samples] == ["start", "end"]
    assert all(sample.task_id == "task-123" for sample in samples)
    rendered = metrics.render().decode("utf-8")
    assert 'service="payments-api"' in rendered
    assert "relayna_tasks_started_total" in rendered
    assert "relayna_tasks_completed_total" in rendered
    assert 'task_id="' not in rendered


@pytest.mark.asyncio
async def test_task_consumer_declares_queue_with_topology_argument_fields() -> None:
    message = FakeMessage(json.dumps({"task_id": "task-123", "payload": {"kind": "demo"}}).encode("utf-8"))
    queue = FakeQueue([message])
    channel = FakeChannel(queue)
    topology = SharedTasksSharedStatusTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.queue",
        tasks_routing_key="task.request",
        status_exchange="status.exchange",
        status_queue="status.queue",
        task_consumer_timeout_ms=600000,
        task_single_active_consumer=True,
        task_max_priority=9,
        task_queue_type="quorum",
        task_queue_arguments_overrides={"x-queue-mode": "lazy"},
        task_queue_kwargs={"x-overflow": "reject-publish"},
    )
    rabbit = FakeRabbitClient(topology=topology, acquire_results=[channel])

    async def handler(task: Any, context: TaskContext) -> None:
        del task, context

    consumer = TaskConsumer(rabbitmq=rabbit, handler=handler)

    await run_consumer_until_message_done(consumer, message)

    assert channel.declare_queue_calls == [
        {
            "name": "tasks.queue",
            "durable": True,
            "arguments": {
                "x-consumer-timeout": 600000,
                "x-single-active-consumer": True,
                "x-max-priority": 9,
                "x-queue-type": "quorum",
                "x-queue-mode": "lazy",
                "x-overflow": "reject-publish",
            },
        }
    ]


@pytest.mark.asyncio
async def test_task_consumer_rejects_malformed_json_without_requeue() -> None:
    message = FakeMessage(b"{not-json")
    queue = FakeQueue([message])
    rabbit = FakeRabbitClient(topology=make_topology(), acquire_results=[FakeChannel(queue)])
    handled = False

    async def handler(task: Any, context: TaskContext) -> None:
        nonlocal handled
        handled = True

    consumer = TaskConsumer(rabbitmq=rabbit, handler=handler)

    await run_consumer_until_message_done(consumer, message)

    assert handled is False
    assert message.acked is False
    assert message.rejected_with is False


@pytest.mark.asyncio
async def test_task_consumer_rejects_invalid_envelope_without_requeue() -> None:
    message = FakeMessage(json.dumps({"payload": {"kind": "demo"}}).encode("utf-8"))
    queue = FakeQueue([message])
    rabbit = FakeRabbitClient(topology=make_topology(), acquire_results=[FakeChannel(queue)])

    async def handler(task: Any, context: TaskContext) -> None:
        raise AssertionError("handler should not run")

    consumer = TaskConsumer(rabbitmq=rabbit, handler=handler)

    await run_consumer_until_message_done(consumer, message)

    assert message.acked is False
    assert message.rejected_with is False


@pytest.mark.asyncio
async def test_task_consumer_rejects_handler_errors_by_default() -> None:
    message = FakeMessage(json.dumps({"task_id": "task-123"}).encode("utf-8"))
    queue = FakeQueue([message])
    rabbit = FakeRabbitClient(topology=make_topology(), acquire_results=[FakeChannel(queue)])

    async def handler(task: Any, context: TaskContext) -> None:
        raise RuntimeError("boom")

    consumer = TaskConsumer(rabbitmq=rabbit, handler=handler)

    await run_consumer_until_message_done(consumer, message)

    assert message.acked is False
    assert message.rejected_with is False
    assert rabbit.published_statuses == []


@pytest.mark.asyncio
async def test_task_consumer_can_requeue_handler_errors() -> None:
    message = FakeMessage(json.dumps({"task_id": "task-123"}).encode("utf-8"))
    queue = FakeQueue([message])
    rabbit = FakeRabbitClient(topology=make_topology(), acquire_results=[FakeChannel(queue)])

    async def handler(task: Any, context: TaskContext) -> None:
        raise RuntimeError("boom")

    consumer = TaskConsumer(rabbitmq=rabbit, handler=handler, failure_action=FailureAction.REQUEUE)

    await run_consumer_until_message_done(consumer, message)

    assert message.rejected_with is True


@pytest.mark.asyncio
async def test_task_consumer_publishes_lifecycle_statuses_when_enabled() -> None:
    message = FakeMessage(json.dumps({"task_id": "task-123"}).encode("utf-8"), correlation_id="corr-123")
    queue = FakeQueue([message])
    rabbit = FakeRabbitClient(topology=make_topology(), acquire_results=[FakeChannel(queue)])
    order: list[str] = []

    async def handler(task: Any, context: TaskContext) -> None:
        order.append("handler")

    consumer = TaskConsumer(
        rabbitmq=rabbit,
        handler=handler,
        lifecycle_statuses=LifecycleStatusConfig(enabled=True),
    )

    await run_consumer_until_message_done(consumer, message)

    assert [event["status"] for event in rabbit.published_statuses] == ["processing", "completed"]
    assert rabbit.published_statuses[0]["correlation_id"] == "corr-123"
    assert rabbit.published_statuses[0]["task_id"] == "task-123"
    assert order == ["handler"]


@pytest.mark.asyncio
async def test_task_consumer_publishes_failed_status_on_handler_error() -> None:
    message = FakeMessage(json.dumps({"task_id": "task-123"}).encode("utf-8"))
    queue = FakeQueue([message])
    rabbit = FakeRabbitClient(topology=make_topology(), acquire_results=[FakeChannel(queue)])

    async def handler(task: Any, context: TaskContext) -> None:
        raise RuntimeError("handler exploded")

    consumer = TaskConsumer(
        rabbitmq=rabbit,
        handler=handler,
        lifecycle_statuses=LifecycleStatusConfig(enabled=True),
    )

    await run_consumer_until_message_done(consumer, message)

    assert [event["status"] for event in rabbit.published_statuses] == ["processing", "failed"]
    assert rabbit.published_statuses[1]["message"] == "handler exploded"


@pytest.mark.asyncio
async def test_task_consumer_does_not_publish_lifecycle_statuses_when_disabled() -> None:
    message = FakeMessage(json.dumps({"task_id": "task-123"}).encode("utf-8"))
    queue = FakeQueue([message])
    rabbit = FakeRabbitClient(topology=make_topology(), acquire_results=[FakeChannel(queue)])

    async def handler(task: Any, context: TaskContext) -> None:
        return None

    consumer = TaskConsumer(
        rabbitmq=rabbit,
        handler=handler,
        lifecycle_statuses=LifecycleStatusConfig(enabled=False),
    )

    await run_consumer_until_message_done(consumer, message)

    assert rabbit.published_statuses == []


@pytest.mark.asyncio
async def test_task_consumer_retries_after_unexpected_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    message = FakeMessage(json.dumps({"task_id": "task-123"}).encode("utf-8"))
    queue = FakeQueue([message])
    channel = FakeChannel(queue)
    rabbit = FakeRabbitClient(
        topology=make_topology(),
        acquire_results=[RuntimeError("temporary failure"), channel],
    )
    sleep_calls: list[float] = []
    original_sleep = asyncio.sleep

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)
        await original_sleep(0)

    async def handler(task: Any, context: TaskContext) -> None:
        return None

    monkeypatch.setattr("relayna.consumer.task_consumer.asyncio.sleep", fake_sleep)
    consumer = TaskConsumer(rabbitmq=rabbit, handler=handler, idle_retry_seconds=0.25)

    await run_consumer_until_message_done(consumer, message)

    assert sleep_calls[0] == 0.25
    assert rabbit.acquire_channel_calls == [1, 1]


@pytest.mark.asyncio
async def test_task_consumer_stop_exits_idle_loop_cleanly() -> None:
    queue = FakeQueue([])
    rabbit = FakeRabbitClient(topology=make_topology(), acquire_results=[FakeChannel(queue), FakeChannel(queue)])

    async def handler(task: Any, context: TaskContext) -> None:
        raise AssertionError("handler should not run")

    consumer = TaskConsumer(rabbitmq=rabbit, handler=handler, idle_retry_seconds=0)
    task = asyncio.create_task(consumer.run_forever())
    await asyncio.sleep(0)
    consumer.stop()
    await task

    assert rabbit.ensure_tasks_queue_calls == 1


@pytest.mark.asyncio
async def test_task_consumer_uses_default_consume_timeout() -> None:
    message = FakeMessage(json.dumps({"task_id": "task-123"}).encode("utf-8"))
    queue = FakeQueue([message])
    rabbit = FakeRabbitClient(topology=make_topology(), acquire_results=[FakeChannel(queue)])

    async def handler(task: Any, context: TaskContext) -> None:
        del task, context

    consumer = TaskConsumer(rabbitmq=rabbit, handler=handler)

    await run_consumer_until_message_done(consumer, message)

    assert queue.iterator_calls == [{"arguments": None, "timeout": 1.0}]


@pytest.mark.asyncio
async def test_task_consumer_allows_indefinite_consume_timeout() -> None:
    message = FakeMessage(json.dumps({"task_id": "task-123"}).encode("utf-8"))
    queue = FakeQueue([message])
    rabbit = FakeRabbitClient(topology=make_topology(), acquire_results=[FakeChannel(queue)])

    async def handler(task: Any, context: TaskContext) -> None:
        del task, context

    consumer = TaskConsumer(rabbitmq=rabbit, handler=handler, consume_timeout_seconds=None)

    await run_consumer_until_message_done(consumer, message)

    assert queue.iterator_calls == [{"arguments": None, "timeout": None}]


def test_fake_queue_helper_signature_supports_consumer_timeout() -> None:
    queue = FakeQueue([])
    iterator = queue.iterator(arguments={"x-priority": 1}, timeout=1.0)

    assert isinstance(iterator, FakeIterator)
    assert queue.iterator_calls == [{"arguments": {"x-priority": 1}, "timeout": 1.0}]


@pytest.mark.asyncio
async def test_task_consumer_emits_observations_for_successful_processing() -> None:
    message = FakeMessage(json.dumps({"task_id": "task-123"}).encode("utf-8"))
    queue = FakeQueue([message])
    rabbit = FakeRabbitClient(topology=make_topology(), acquire_results=[FakeChannel(queue)])
    observed: list[object] = []

    async def sink(event: object) -> None:
        observed.append(event)

    async def handler(task: Any, context: TaskContext) -> None:
        return None

    consumer = TaskConsumer(
        rabbitmq=rabbit,
        handler=handler,
        lifecycle_statuses=LifecycleStatusConfig(enabled=True),
        observation_sink=sink,
    )

    await run_consumer_until_message_done(consumer, message)

    assert isinstance(observed[0], TaskConsumerStarted)
    assert isinstance(observed[1], TaskMessageReceived)
    lifecycle_events = [event for event in observed if isinstance(event, TaskLifecycleStatusPublished)]
    assert [event.status for event in lifecycle_events] == ["processing", "completed"]
    resource_samples = [event for event in observed if isinstance(event, TaskResourceSampled)]
    assert [event.sample_kind for event in resource_samples] == ["start", "end"]
    assert isinstance(observed[-1], TaskMessageAcked)


@pytest.mark.asyncio
async def test_task_consumer_emits_rejected_and_failed_observations() -> None:
    malformed = FakeMessage(b"{not-json")
    malformed_queue = FakeQueue([malformed])
    malformed_rabbit = FakeRabbitClient(topology=make_topology(), acquire_results=[FakeChannel(malformed_queue)])
    malformed_observed: list[object] = []

    async def sink(event: object) -> None:
        malformed_observed.append(event)

    async def no_op_handler(task: Any, context: TaskContext) -> None:
        return None

    malformed_consumer = TaskConsumer(
        rabbitmq=malformed_rabbit,
        handler=no_op_handler,
        observation_sink=sink,
    )
    await run_consumer_until_message_done(malformed_consumer, malformed)
    assert isinstance(malformed_observed[1], TaskMessageRejected)
    assert malformed_observed[1].reason == "malformed_json"

    failed = FakeMessage(json.dumps({"task_id": "task-123"}).encode("utf-8"))
    failed_queue = FakeQueue([failed])
    failed_rabbit = FakeRabbitClient(topology=make_topology(), acquire_results=[FakeChannel(failed_queue)])
    failed_observed: list[object] = []

    async def failing_sink(event: object) -> None:
        failed_observed.append(event)

    async def failing_handler(task: Any, context: TaskContext) -> None:
        raise RuntimeError("boom")

    failed_consumer = TaskConsumer(
        rabbitmq=failed_rabbit,
        handler=failing_handler,
        observation_sink=failing_sink,
    )
    await run_consumer_until_message_done(failed_consumer, failed)
    handler_failed = next(event for event in failed_observed if isinstance(event, TaskHandlerFailed))
    assert handler_failed.exception_message == "boom"
    rejected = [event for event in failed_observed if isinstance(event, TaskMessageRejected)]
    assert rejected[-1].reason == "handler_error"


@pytest.mark.asyncio
async def test_task_consumer_emits_loop_error_observation(monkeypatch: pytest.MonkeyPatch) -> None:
    message = FakeMessage(json.dumps({"task_id": "task-123"}).encode("utf-8"))
    queue = FakeQueue([message])
    channel = FakeChannel(queue)
    rabbit = FakeRabbitClient(topology=make_topology(), acquire_results=[RuntimeError("temporary failure"), channel])
    observed: list[object] = []
    original_sleep = asyncio.sleep

    async def sink(event: object) -> None:
        observed.append(event)

    async def fake_sleep(delay: float) -> None:
        await original_sleep(0)

    async def handler(task: Any, context: TaskContext) -> None:
        return None

    monkeypatch.setattr("relayna.consumer.task_consumer.asyncio.sleep", fake_sleep)
    consumer = TaskConsumer(rabbitmq=rabbit, handler=handler, idle_retry_seconds=0.25, observation_sink=sink)

    await run_consumer_until_message_done(consumer, message)

    loop_errors = [event for event in observed if isinstance(event, TaskConsumerLoopError)]
    assert loop_errors
    assert loop_errors[0].retry_delay_seconds == 0.25
    assert loop_errors[0].exception_message == "temporary failure"


@pytest.mark.asyncio
async def test_task_consumer_sink_failures_do_not_break_processing() -> None:
    message = FakeMessage(json.dumps({"task_id": "task-123"}).encode("utf-8"))
    queue = FakeQueue([message])
    rabbit = FakeRabbitClient(topology=make_topology(), acquire_results=[FakeChannel(queue)])

    async def sink(event: object) -> None:
        raise RuntimeError("sink failed")

    async def handler(task: Any, context: TaskContext) -> None:
        return None

    consumer = TaskConsumer(rabbitmq=rabbit, handler=handler, observation_sink=sink)

    await run_consumer_until_message_done(consumer, message)


@pytest.mark.asyncio
async def test_task_consumer_declares_retry_infrastructure_when_enabled() -> None:
    message = FakeMessage(json.dumps({"task_id": "task-123"}).encode("utf-8"))
    queue = FakeQueue([message])
    rabbit = FakeRabbitClient(topology=make_topology(), acquire_results=[FakeChannel(queue)])

    async def handler(task: Any, context: TaskContext) -> None:
        return None

    consumer = TaskConsumer(
        rabbitmq=rabbit,
        handler=handler,
        retry_policy=RetryPolicy(max_retries=2, delay_ms=5000),
    )

    await run_consumer_until_message_done(consumer, message)

    assert rabbit.retry_infrastructure_calls == [
        {
            "source_queue_name": "tasks.queue",
            "delay_ms": 5000,
            "retry_queue_suffix": ".retry",
            "dead_letter_queue_suffix": ".dlq",
        }
    ]


@pytest.mark.asyncio
async def test_task_consumer_republishes_failed_messages_to_retry_queue() -> None:
    message = FakeMessage(
        json.dumps({"task_id": "task-123"}).encode("utf-8"),
        correlation_id="corr-123",
        headers={"x-relayna-retry-attempt": 1},
    )
    queue = FakeQueue([message])
    rabbit = FakeRabbitClient(topology=make_topology(), acquire_results=[FakeChannel(queue)])
    observed: list[object] = []
    seen_context: list[tuple[int, int | None, str | None]] = []

    async def sink(event: object) -> None:
        observed.append(event)

    async def handler(task: Any, context: TaskContext) -> None:
        seen_context.append((context.retry_attempt, context.max_retries, context.source_queue_name))
        raise RuntimeError("boom")

    consumer = TaskConsumer(
        rabbitmq=rabbit,
        handler=handler,
        retry_policy=RetryPolicy(max_retries=3, delay_ms=1000),
        retry_statuses=RetryStatusConfig(enabled=True),
        observation_sink=sink,
    )

    await run_consumer_until_message_done(consumer, message)

    assert seen_context == [(1, 3, "tasks.queue")]
    assert message.acked is True
    assert message.rejected_with is None
    assert rabbit.raw_queue_publishes[0]["queue_name"] == "tasks.queue.retry"
    assert rabbit.raw_queue_publishes[0]["body"] == message.body
    assert rabbit.raw_queue_publishes[0]["correlation_id"] == "corr-123"
    assert rabbit.raw_queue_publishes[0]["headers"]["x-relayna-retry-attempt"] == 2
    assert rabbit.raw_queue_publishes[0]["headers"]["x-relayna-max-retries"] == 3
    assert rabbit.raw_queue_publishes[0]["headers"]["x-relayna-source-queue"] == "tasks.queue"
    assert rabbit.raw_queue_publishes[0]["headers"]["x-relayna-failure-reason"] == "handler_error"
    assert rabbit.raw_queue_publishes[0]["headers"]["x-relayna-exception-type"] == "RuntimeError"
    assert [event["status"] for event in rabbit.published_statuses] == ["retrying"]
    assert any(isinstance(event, ConsumerRetryScheduled) for event in observed)


@pytest.mark.asyncio
async def test_task_consumer_dead_letters_exhausted_failed_messages() -> None:
    message = FakeMessage(
        json.dumps({"task_id": "task-123"}).encode("utf-8"),
        headers={"x-relayna-retry-attempt": 3},
    )
    queue = FakeQueue([message])
    rabbit = FakeRabbitClient(topology=make_topology(), acquire_results=[FakeChannel(queue)])
    observed: list[object] = []

    async def sink(event: object) -> None:
        observed.append(event)

    async def handler(task: Any, context: TaskContext) -> None:
        raise RuntimeError("boom")

    consumer = TaskConsumer(
        rabbitmq=rabbit,
        handler=handler,
        retry_policy=RetryPolicy(max_retries=3, delay_ms=1000),
        retry_statuses=RetryStatusConfig(enabled=True),
        observation_sink=sink,
    )

    await run_consumer_until_message_done(consumer, message)

    assert message.acked is True
    assert rabbit.raw_queue_publishes[0]["queue_name"] == "tasks.queue.dlq"
    assert rabbit.raw_queue_publishes[0]["headers"]["x-relayna-retry-attempt"] == 3
    assert rabbit.raw_queue_publishes[0]["headers"]["x-relayna-failure-reason"] == "handler_error"
    assert [event["status"] for event in rabbit.published_statuses] == ["failed"]
    assert rabbit.published_statuses[0]["message"] == "boom"
    assert any(isinstance(event, ConsumerDeadLetterPublished) for event in observed)


@pytest.mark.asyncio
async def test_task_consumer_indexes_dead_lettered_messages() -> None:
    message = FakeMessage(
        json.dumps({"task_id": "task-123", "payload": {"kind": "demo"}}).encode("utf-8"),
        correlation_id="corr-123",
        headers={"x-relayna-retry-attempt": 3},
    )
    queue = FakeQueue([message])
    rabbit = FakeRabbitClient(topology=make_topology(), acquire_results=[FakeChannel(queue)])
    dlq_store = FakeDLQStore()

    async def handler(task: Any, context: TaskContext) -> None:
        raise RuntimeError("boom")

    consumer = TaskConsumer(
        rabbitmq=rabbit,
        handler=handler,
        retry_policy=RetryPolicy(max_retries=3, delay_ms=1000),
        dlq_store=dlq_store,
    )

    await run_consumer_until_message_done(consumer, message)

    assert len(dlq_store.records) == 1
    record = dlq_store.records[0]
    assert record.queue_name == "tasks.queue.dlq"
    assert record.source_queue_name == "tasks.queue"
    assert record.retry_queue_name == "tasks.queue.retry"
    assert record.task_id == "task-123"
    assert record.correlation_id == "corr-123"
    assert record.reason == "handler_error"
    assert record.exception_type == "RuntimeError"
    assert record.retry_attempt == 3
    assert record.max_retries == 3
    assert record.body == {"task_id": "task-123", "payload": {"kind": "demo"}}
    assert record.body_encoding == "json"
    assert record.headers["x-relayna-source-queue"] == "tasks.queue"


@pytest.mark.asyncio
async def test_task_consumer_emits_observation_when_dlq_index_write_fails() -> None:
    message = FakeMessage(
        json.dumps({"task_id": "task-123", "payload": {"kind": "demo"}}).encode("utf-8"),
        correlation_id="corr-123",
        headers={"x-relayna-retry-attempt": 3},
    )
    queue = FakeQueue([message])
    rabbit = FakeRabbitClient(topology=make_topology(), acquire_results=[FakeChannel(queue)])
    dlq_store = FakeDLQStore(fail=True)
    observed: list[object] = []

    async def sink(event: object) -> None:
        observed.append(event)

    async def handler(task: Any, context: TaskContext) -> None:
        raise RuntimeError("boom")

    consumer = TaskConsumer(
        rabbitmq=rabbit,
        handler=handler,
        retry_policy=RetryPolicy(max_retries=3, delay_ms=1000),
        dlq_store=dlq_store,
        observation_sink=sink,
    )

    await run_consumer_until_message_done(consumer, message)

    assert message.acked is True
    assert rabbit.raw_queue_publishes[0]["queue_name"] == "tasks.queue.dlq"
    persist_failed = next(event for event in observed if isinstance(event, ConsumerDLQRecordPersistFailed))
    assert persist_failed.consumer_name == "relayna-task-consumer"
    assert persist_failed.task_id == "task-123"
    assert persist_failed.queue_name == "tasks.queue.dlq"
    assert persist_failed.retry_attempt == 3
    assert persist_failed.max_retries == 3
    assert persist_failed.reason == "handler_error"
    assert persist_failed.exception_type == "RuntimeError"
    assert persist_failed.exception_message == "dlq store unavailable"


@pytest.mark.asyncio
async def test_task_consumer_preserves_lifecycle_failed_status_when_retry_statuses_are_disabled() -> None:
    message = FakeMessage(
        json.dumps({"task_id": "task-123"}).encode("utf-8"),
        headers={"x-relayna-retry-attempt": 1},
    )
    queue = FakeQueue([message])
    rabbit = FakeRabbitClient(topology=make_topology(), acquire_results=[FakeChannel(queue)])

    async def handler(task: Any, context: TaskContext) -> None:
        raise RuntimeError("boom")

    consumer = TaskConsumer(
        rabbitmq=rabbit,
        handler=handler,
        retry_policy=RetryPolicy(max_retries=1, delay_ms=1000),
        lifecycle_statuses=LifecycleStatusConfig(enabled=True),
    )

    await run_consumer_until_message_done(consumer, message)

    assert message.acked is True
    assert rabbit.raw_queue_publishes[0]["queue_name"] == "tasks.queue.dlq"
    assert [event["status"] for event in rabbit.published_statuses] == ["processing", "failed"]
    assert rabbit.published_status_calls[-1][0] == "status"
    assert rabbit.published_statuses[-1]["message"] == "boom"


@pytest.mark.asyncio
async def test_task_consumer_dead_letters_malformed_json_when_retry_enabled() -> None:
    message = FakeMessage(b"{not-json")
    queue = FakeQueue([message])
    rabbit = FakeRabbitClient(topology=make_topology(), acquire_results=[FakeChannel(queue)])

    async def handler(task: Any, context: TaskContext) -> None:
        raise AssertionError("handler should not run")

    consumer = TaskConsumer(
        rabbitmq=rabbit,
        handler=handler,
        retry_policy=RetryPolicy(max_retries=2, delay_ms=1000),
    )

    await run_consumer_until_message_done(consumer, message)

    assert message.acked is True
    assert message.rejected_with is None
    assert rabbit.raw_queue_publishes[0]["queue_name"] == "tasks.queue.dlq"
    assert rabbit.raw_queue_publishes[0]["headers"]["x-relayna-failure-reason"] == "malformed_json"
    assert rabbit.published_statuses == []


@pytest.mark.asyncio
async def test_task_consumer_indexes_malformed_dead_letter_payload_as_text() -> None:
    message = FakeMessage(b"{not-json")
    queue = FakeQueue([message])
    rabbit = FakeRabbitClient(topology=make_topology(), acquire_results=[FakeChannel(queue)])
    dlq_store = FakeDLQStore()

    async def handler(task: Any, context: TaskContext) -> None:
        raise AssertionError("handler should not run")

    consumer = TaskConsumer(
        rabbitmq=rabbit,
        handler=handler,
        retry_policy=RetryPolicy(max_retries=2, delay_ms=1000),
        dlq_store=dlq_store,
    )

    await run_consumer_until_message_done(consumer, message)

    assert len(dlq_store.records) == 1
    record = dlq_store.records[0]
    assert record.reason == "malformed_json"
    assert record.exception_type is None
    assert record.body == "{not-json"
    assert record.body_encoding == "text"


@pytest.mark.asyncio
async def test_task_consumer_dead_letters_invalid_envelope_when_retry_enabled() -> None:
    message = FakeMessage(json.dumps({"payload": {"kind": "demo"}}).encode("utf-8"))
    queue = FakeQueue([message])
    rabbit = FakeRabbitClient(topology=make_topology(), acquire_results=[FakeChannel(queue)])

    async def handler(task: Any, context: TaskContext) -> None:
        raise AssertionError("handler should not run")

    consumer = TaskConsumer(
        rabbitmq=rabbit,
        handler=handler,
        retry_policy=RetryPolicy(max_retries=2, delay_ms=1000),
    )

    await run_consumer_until_message_done(consumer, message)

    assert message.acked is True
    assert rabbit.raw_queue_publishes[0]["queue_name"] == "tasks.queue.dlq"
    assert rabbit.raw_queue_publishes[0]["headers"]["x-relayna-failure-reason"] == "invalid_envelope"
    assert rabbit.raw_queue_publishes[0]["headers"]["x-relayna-exception-type"] == "ValidationError"


@pytest.mark.asyncio
async def test_task_consumer_rejects_batch_envelope_without_retry_policy() -> None:
    message = FakeMessage(
        json.dumps(
            {
                "batch_id": "batch-123",
                "tasks": [{"task_id": "task-123", "payload": {"kind": "demo"}}],
            }
        ).encode("utf-8")
    )
    queue = FakeQueue([message])
    rabbit = FakeRabbitClient(topology=make_topology(), acquire_results=[FakeChannel(queue)])

    async def handler(task: Any, context: TaskContext) -> None:
        raise AssertionError("handler should not run")

    consumer = TaskConsumer(rabbitmq=rabbit, handler=handler)

    await run_consumer_until_message_done(consumer, message)

    assert message.rejected_with is False
    assert not rabbit.raw_queue_publishes


@pytest.mark.asyncio
async def test_task_consumer_fans_out_batch_envelope_into_individual_messages() -> None:
    message = FakeMessage(
        json.dumps(
            {
                "batch_id": "batch-123",
                "tasks": [
                    {"task_id": "task-1", "payload": {"kind": "demo"}},
                    {"task_id": "task-2", "payload": {"kind": "demo"}},
                ],
            }
        ).encode("utf-8"),
        correlation_id="batch-123",
    )
    queue = FakeQueue([message])
    rabbit = FakeRabbitClient(topology=make_topology(), acquire_results=[FakeChannel(queue)])

    async def handler(task: Any, context: TaskContext) -> None:
        raise AssertionError("handler should not run for the original batch envelope")

    consumer = TaskConsumer(
        rabbitmq=rabbit,
        handler=handler,
        retry_policy=RetryPolicy(max_retries=2, delay_ms=1000),
    )

    await run_consumer_until_message_done(consumer, message)

    assert message.acked is True
    assert len(rabbit.raw_queue_publishes) == 2
    assert [publish["queue_name"] for publish in rabbit.raw_queue_publishes] == ["tasks.queue", "tasks.queue"]
    first_payload = json.loads(rabbit.raw_queue_publishes[0]["body"].decode("utf-8"))
    second_payload = json.loads(rabbit.raw_queue_publishes[1]["body"].decode("utf-8"))
    assert first_payload["task_id"] == "task-1"
    assert first_payload["payload"] == {"kind": "demo"}
    assert first_payload["spec_version"] == "1.0"
    assert isinstance(first_payload["created_at"], str)
    assert second_payload["task_id"] == "task-2"
    assert second_payload["payload"] == {"kind": "demo"}
    assert second_payload["spec_version"] == "1.0"
    assert isinstance(second_payload["created_at"], str)
    assert rabbit.raw_queue_publishes[0]["headers"] == {
        "task_id": "task-1",
        "batch_id": "batch-123",
        "batch_index": 0,
        "batch_size": 2,
    }
    assert rabbit.raw_queue_publishes[1]["headers"] == {
        "task_id": "task-2",
        "batch_id": "batch-123",
        "batch_index": 1,
        "batch_size": 2,
    }


@pytest.mark.asyncio
async def test_task_consumer_dead_letters_mixed_task_type_batch_for_routed_topology() -> None:
    message = FakeMessage(
        json.dumps(
            {
                "batch_id": "batch-123",
                "tasks": [
                    {"task_id": "task-1", "task_type": "task.review", "payload": {"kind": "demo"}},
                    {"task_id": "task-2", "task_type": "task.audit", "payload": {"kind": "demo"}},
                ],
            }
        ).encode("utf-8"),
        correlation_id="batch-123",
    )
    queue = FakeQueue([message])
    topology = RoutedTasksSharedStatusTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.review.queue",
        status_exchange="status.exchange",
        status_queue="status.queue",
        task_types=("task.review", "task.audit"),
    )
    rabbit = FakeRabbitClient(topology=topology, queue_name="tasks.review.queue", acquire_results=[FakeChannel(queue)])

    async def handler(task: Any, context: TaskContext) -> None:
        raise AssertionError("handler should not run for mixed routed batch envelopes")

    consumer = TaskConsumer(
        rabbitmq=rabbit,
        handler=handler,
        retry_policy=RetryPolicy(max_retries=2, delay_ms=1000),
    )

    await run_consumer_until_message_done(consumer, message)

    assert message.acked is True
    assert not rabbit.published_tasks
    assert len(rabbit.raw_queue_publishes) == 1
    assert rabbit.raw_queue_publishes[0]["queue_name"] == "tasks.review.queue.dlq"
    assert rabbit.raw_queue_publishes[0]["headers"]["x-relayna-failure-reason"] == "mixed_task_type_batch"


@pytest.mark.asyncio
async def test_task_consumer_retries_failed_batch_item_using_headers() -> None:
    message = FakeMessage(
        json.dumps({"task_id": "task-1", "payload": {"kind": "demo"}}).encode("utf-8"),
        correlation_id="task-1",
        headers={"task_id": "task-1", "batch_id": "batch-123", "batch_index": 0, "batch_size": 2},
    )
    queue = FakeQueue([message])
    rabbit = FakeRabbitClient(topology=make_topology(), acquire_results=[FakeChannel(queue)])
    handled: list[tuple[str, str | None, int | None, int | None]] = []

    async def handler(task: Any, context: TaskContext) -> None:
        handled.append((task.task_id, context.batch_id, context.batch_index, context.batch_size))
        raise RuntimeError("boom")

    consumer = TaskConsumer(
        rabbitmq=rabbit,
        handler=handler,
        retry_policy=RetryPolicy(max_retries=2, delay_ms=1000),
    )

    await run_consumer_until_message_done(consumer, message)

    assert message.acked is True
    assert handled == [("task-1", "batch-123", 0, 2)]
    assert len(rabbit.raw_queue_publishes) == 1
    assert rabbit.raw_queue_publishes[0]["queue_name"] == "tasks.queue.retry"
    republished = json.loads(rabbit.raw_queue_publishes[0]["body"].decode("utf-8"))
    assert republished["task_id"] == "task-1"
    assert republished["payload"] == {"kind": "demo"}
    assert rabbit.raw_queue_publishes[0]["headers"]["task_id"] == "task-1"
    assert rabbit.raw_queue_publishes[0]["headers"]["batch_id"] == "batch-123"
    assert rabbit.raw_queue_publishes[0]["headers"]["batch_index"] == 0
    assert rabbit.raw_queue_publishes[0]["headers"]["batch_size"] == 2
    assert rabbit.raw_queue_publishes[0]["headers"]["x-relayna-retry-attempt"] == 1


@pytest.mark.asyncio
async def test_aggregation_worker_runtime_can_own_rabbitmq_lifecycle() -> None:
    rabbit = FakeRabbitClient(topology=make_topology(), acquire_results=[])

    async def handler(task: Any, context: TaskContext) -> None:
        return None

    runtime = AggregationWorkerRuntime(
        rabbitmq=rabbit,
        handler=handler,
        shard_groups=[[0], [1, 2]],
    )

    await runtime.start()
    await runtime.stop()

    assert rabbit.initialize_calls == 1
    assert rabbit.close_calls == 0


def test_aggregation_worker_runtime_forwards_consume_timeout_to_consumers() -> None:
    rabbit = FakeRabbitClient(topology=make_topology(), acquire_results=[])

    async def handler(task: Any, context: TaskContext) -> None:
        del task, context

    runtime = AggregationWorkerRuntime(
        rabbitmq=rabbit,
        handler=handler,
        shard_groups=[[0], [1, 2]],
        consume_timeout_seconds=None,
    )

    assert [consumer._consume_timeout_seconds for consumer in runtime._consumers] == [None, None]


@pytest.mark.asyncio
async def test_aggregation_consumer_preserves_manual_retry_lineage_from_event_meta() -> None:
    event = {
        "task_id": "task-123",
        "status": "processing",
        "meta": {
            "manual_retry": {
                "count": 2,
                "previous_task_type": "task.generate",
                "source_consumer": "worker-a",
                "reason": "quality gate failed",
            }
        },
    }
    message = FakeMessage(json.dumps(event).encode("utf-8"), correlation_id="task-123")
    queue = FakeQueue([message])
    rabbit = FakeRabbitClient(topology=make_topology(), acquire_results=[FakeChannel(queue)])

    async def handler(status_event: Any, context: TaskContext) -> None:
        await context.publish_aggregation_status(status="aggregate", meta=dict(status_event.meta))

    consumer = AggregationConsumer(
        rabbitmq=rabbit,
        handler=handler,
        shards=[0],
        retry_statuses=RetryStatusConfig(enabled=True),
    )

    await run_consumer_until_message_done(consumer, message)

    assert message.acked is True
    assert rabbit.published_status_calls[0][0] == "aggregation"
    assert rabbit.published_statuses[0]["meta"]["manual_retry"] == {
        "count": 2,
        "previous_task_type": "task.generate",
        "source_consumer": "worker-a",
        "reason": "quality gate failed",
    }


@pytest.mark.asyncio
async def test_aggregation_consumer_tolerates_non_numeric_manual_retry_count_in_meta() -> None:
    event = {
        "task_id": "task-123",
        "status": "processing",
        "meta": {
            "manual_retry": {
                "count": "not-a-number",
                "previous_task_type": "task.generate",
                "source_consumer": "worker-a",
            }
        },
    }
    message = FakeMessage(json.dumps(event).encode("utf-8"), correlation_id="task-123")
    queue = FakeQueue([message])
    rabbit = FakeRabbitClient(topology=make_topology(), acquire_results=[FakeChannel(queue)])

    async def handler(status_event: Any, context: TaskContext) -> None:
        await context.publish_aggregation_status(status="aggregate", meta=dict(status_event.meta))

    consumer = AggregationConsumer(
        rabbitmq=rabbit,
        handler=handler,
        shards=[0],
        retry_statuses=RetryStatusConfig(enabled=True),
    )

    await run_consumer_until_message_done(consumer, message)

    assert message.acked is True
    assert rabbit.published_statuses[0]["meta"]["manual_retry"] == {
        "count": 0,
        "previous_task_type": "task.generate",
        "source_consumer": "worker-a",
    }


@pytest.mark.asyncio
async def test_aggregation_worker_runtime_stop_cancels_stuck_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    rabbit = FakeRabbitClient(topology=make_topology(), acquire_results=[])

    async def handler(task: Any, context: TaskContext) -> None:
        return None

    runtime = AggregationWorkerRuntime(
        rabbitmq=rabbit,
        handler=handler,
        shard_groups=[[0]],
    )
    stuck = asyncio.create_task(asyncio.Event().wait())
    runtime._tasks = [stuck]

    async def fake_wait_for(awaitable: object, timeout: float) -> object:
        del awaitable, timeout
        raise TimeoutError

    monkeypatch.setattr("relayna.consumer.lifecycle.asyncio.wait_for", fake_wait_for)

    await runtime.stop()

    assert stuck.cancelled()
    assert runtime._tasks == []


@pytest.mark.asyncio
async def test_aggregation_consumer_ensures_custom_queue_bindings() -> None:
    message = FakeMessage(json.dumps({"task_id": "task-123", "status": "done"}).encode("utf-8"))
    queue = FakeQueue([message])
    channel = FakeChannel(queue)
    rabbit = FakeRabbitClient(topology=make_topology(), acquire_results=[channel])

    async def handler(task: Any, context: TaskContext) -> None:
        return None

    consumer = AggregationConsumer(
        rabbitmq=rabbit,
        handler=handler,
        shards=[1, 2],
        queue_name="aggregation.queue.custom",
    )

    await run_consumer_until_message_done(consumer, message)

    expected = {"shards": [1, 2], "queue_name": "aggregation.queue.custom"}
    assert rabbit.aggregation_queue_calls
    assert all(call == expected for call in rabbit.aggregation_queue_calls)
    assert channel.declare_queue_calls[0]["name"] == "aggregation.queue.custom"


@pytest.mark.asyncio
async def test_aggregation_consumer_declares_queue_with_topology_argument_fields() -> None:
    message = FakeMessage(json.dumps({"task_id": "task-123", "status": "done"}).encode("utf-8"))
    queue = FakeQueue([message])
    channel = FakeChannel(queue)
    topology = SharedTasksSharedStatusShardedAggregationTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.queue",
        tasks_routing_key="task.request",
        status_exchange="status.exchange",
        status_queue="status.queue",
        shard_count=4,
        aggregation_consumer_timeout_ms=120000,
        aggregation_single_active_consumer=True,
        aggregation_max_priority=5,
        aggregation_queue_type="quorum",
        aggregation_queue_arguments_overrides={"x-delivery-limit": 20},
        aggregation_queue_kwargs={"x-overflow": "reject-publish"},
    )
    rabbit = FakeRabbitClient(topology=topology, acquire_results=[channel])

    async def handler(task: Any, context: TaskContext) -> None:
        del task, context

    consumer = AggregationConsumer(rabbitmq=rabbit, handler=handler, shards=[0])

    await run_consumer_until_message_done(consumer, message)

    assert channel.declare_queue_calls == [
        {
            "name": "aggregation.queue.0",
            "durable": True,
            "arguments": {
                "x-consumer-timeout": 120000,
                "x-single-active-consumer": True,
                "x-max-priority": 5,
                "x-queue-type": "quorum",
                "x-delivery-limit": 20,
                "x-overflow": "reject-publish",
            },
        }
    ]


@pytest.mark.asyncio
async def test_aggregation_consumer_uses_default_consume_timeout() -> None:
    message = FakeMessage(json.dumps({"task_id": "task-123", "status": "done"}).encode("utf-8"))
    queue = FakeQueue([message])
    rabbit = FakeRabbitClient(topology=make_topology(), acquire_results=[FakeChannel(queue)])

    async def handler(task: Any, context: TaskContext) -> None:
        del task, context

    consumer = AggregationConsumer(rabbitmq=rabbit, handler=handler, shards=[0])

    await run_consumer_until_message_done(consumer, message)

    assert queue.iterator_calls == [{"arguments": None, "timeout": 1.0}]


@pytest.mark.asyncio
async def test_aggregation_consumer_allows_indefinite_consume_timeout() -> None:
    message = FakeMessage(json.dumps({"task_id": "task-123", "status": "done"}).encode("utf-8"))
    queue = FakeQueue([message])
    rabbit = FakeRabbitClient(topology=make_topology(), acquire_results=[FakeChannel(queue)])

    async def handler(task: Any, context: TaskContext) -> None:
        del task, context

    consumer = AggregationConsumer(
        rabbitmq=rabbit,
        handler=handler,
        shards=[0],
        consume_timeout_seconds=None,
    )

    await run_consumer_until_message_done(consumer, message)

    assert queue.iterator_calls == [{"arguments": None, "timeout": None}]


@pytest.mark.asyncio
async def test_aggregation_consumer_rejects_failed_messages_without_requeue() -> None:
    message = FakeMessage(json.dumps({"task_id": "task-123", "status": "done"}).encode("utf-8"))
    queue = FakeQueue([message])
    channel = FakeChannel(queue)
    rabbit = FakeRabbitClient(topology=make_topology(), acquire_results=[channel])

    async def handler(task: Any, context: TaskContext) -> None:
        raise RuntimeError("boom")

    consumer = AggregationConsumer(rabbitmq=rabbit, handler=handler, shards=[0])

    await run_consumer_until_message_done(consumer, message)

    assert message.acked is False
    assert message.rejected_with is False


@pytest.mark.asyncio
async def test_aggregation_consumer_retries_failed_messages_and_preserves_parent_metadata() -> None:
    message = FakeMessage(
        json.dumps({"task_id": "task-123", "status": "done", "meta": {"parent_task_id": "parent-1"}}).encode("utf-8")
    )
    queue = FakeQueue([message])
    channel = FakeChannel(queue)
    rabbit = FakeRabbitClient(topology=make_topology(), acquire_results=[channel])
    observations: list[object] = []

    async def sink(event: object) -> None:
        observations.append(event)

    async def handler(task: Any, context: TaskContext) -> None:
        raise RuntimeError("agg failed")

    consumer = AggregationConsumer(
        rabbitmq=rabbit,
        handler=handler,
        shards=[0],
        retry_policy=RetryPolicy(max_retries=2, delay_ms=1000),
        retry_statuses=RetryStatusConfig(enabled=True),
        observation_sink=sink,
    )

    await run_consumer_until_message_done(consumer, message)

    assert message.acked is True
    assert rabbit.raw_queue_publishes[0]["queue_name"] == "aggregation.queue.0.retry"
    assert rabbit.published_statuses[0]["status"] == "retrying"
    assert rabbit.published_statuses[0]["meta"]["parent_task_id"] == "parent-1"
    assert rabbit.published_status_calls[0][0] == "status"
    handler_failed = next(event for event in observations if isinstance(event, AggregationHandlerFailed))
    assert handler_failed.exception_message == "agg failed"


@pytest.mark.asyncio
async def test_aggregation_consumer_dead_letters_exhausted_messages_and_preserves_parent_metadata() -> None:
    message = FakeMessage(
        json.dumps({"task_id": "task-123", "status": "done", "meta": {"parent_task_id": "parent-1"}}).encode("utf-8"),
        headers={"x-relayna-retry-attempt": 2},
    )
    queue = FakeQueue([message])
    channel = FakeChannel(queue)
    rabbit = FakeRabbitClient(topology=make_topology(), acquire_results=[channel])

    async def handler(task: Any, context: TaskContext) -> None:
        raise RuntimeError("agg failed")

    consumer = AggregationConsumer(
        rabbitmq=rabbit,
        handler=handler,
        shards=[0],
        retry_policy=RetryPolicy(max_retries=2, delay_ms=1000),
        retry_statuses=RetryStatusConfig(enabled=True),
    )

    await run_consumer_until_message_done(consumer, message)

    assert message.acked is True
    assert rabbit.raw_queue_publishes[0]["queue_name"] == "aggregation.queue.0.dlq"
    assert rabbit.published_statuses[0]["status"] == "failed"
    assert rabbit.published_statuses[0]["meta"]["parent_task_id"] == "parent-1"
    assert rabbit.published_status_calls[0][0] == "status"


@pytest.mark.asyncio
async def test_aggregation_consumer_indexes_dead_lettered_messages() -> None:
    message = FakeMessage(
        json.dumps({"task_id": "task-123", "status": "done", "meta": {"parent_task_id": "parent-1"}}).encode("utf-8"),
        headers={"x-relayna-retry-attempt": 2},
    )
    queue = FakeQueue([message])
    channel = FakeChannel(queue)
    rabbit = FakeRabbitClient(topology=make_topology(), acquire_results=[channel])
    dlq_store = FakeDLQStore()

    async def handler(task: Any, context: TaskContext) -> None:
        raise RuntimeError("agg failed")

    consumer = AggregationConsumer(
        rabbitmq=rabbit,
        handler=handler,
        shards=[0],
        retry_policy=RetryPolicy(max_retries=2, delay_ms=1000),
        dlq_store=dlq_store,
    )

    await run_consumer_until_message_done(consumer, message)

    assert len(dlq_store.records) == 1
    record = dlq_store.records[0]
    assert record.queue_name == "aggregation.queue.0.dlq"
    assert record.source_queue_name == "aggregation.queue.0"
    assert record.retry_queue_name == "aggregation.queue.0.retry"
    assert record.reason == "handler_error"
    assert record.body["meta"]["parent_task_id"] == "parent-1"


@pytest.mark.asyncio
async def test_aggregation_worker_runtime_requires_topology_or_rabbitmq() -> None:
    async def handler(task: Any, context: TaskContext) -> None:
        return None

    with pytest.raises(ValueError, match="rabbitmq=.*topology"):
        AggregationWorkerRuntime(handler=handler, shard_groups=[[0]])


@pytest.mark.asyncio
async def test_workflow_consumer_consumes_stage_queue_and_emits_workflow_observations() -> None:
    message = FakeMessage(
        json.dumps(
            {
                "task_id": "task-123",
                "message_id": "msg-123",
                "stage": "docsearch_planner",
                "origin_stage": "topic_planner",
                "payload": {"docs": ["a"]},
            }
        ).encode("utf-8"),
        correlation_id="corr-123",
    )
    queue = FakeQueue([message])
    channel = FakeChannel(queue)
    rabbit = FakeRabbitClient(topology=make_workflow_topology(), acquire_results=[channel])
    observations: list[object] = []

    async def sink(event: object) -> None:
        observations.append(event)

    async def handler(workflow: WorkflowEnvelope, context: WorkflowContext) -> None:
        assert workflow.stage == "docsearch_planner"
        assert context.stage == "docsearch_planner"
        await context.publish_status(status="processing", message="Started.")

    consumer = WorkflowConsumer(
        rabbitmq=rabbit,
        handler=handler,
        stage="docsearch_planner",
        observation_sink=sink,
    )

    await run_consumer_until_message_done(consumer, message)

    assert message.acked is True
    assert rabbit.workflow_queue_calls == ["docsearch_planner"]
    assert channel.declare_queue_calls[0]["name"] == "cq.docsearch_planner.in_queue"
    assert isinstance(observations[0], WorkflowStageStarted)
    assert any(isinstance(event, WorkflowMessageReceived) for event in observations)
    assert any(isinstance(event, WorkflowStageAcked) for event in observations)


@pytest.mark.asyncio
async def test_workflow_consumer_schedules_retry_and_status_on_handler_failure() -> None:
    message = FakeMessage(
        json.dumps(
            {
                "task_id": "task-123",
                "message_id": "msg-123",
                "stage": "docsearch_planner",
                "origin_stage": "topic_planner",
                "payload": {"docs": ["a"]},
            }
        ).encode("utf-8"),
        correlation_id="corr-123",
    )
    queue = FakeQueue([message])
    channel = FakeChannel(queue)
    rabbit = FakeRabbitClient(topology=make_workflow_topology(), acquire_results=[channel])
    observations: list[object] = []

    async def sink(event: object) -> None:
        observations.append(event)

    async def handler(workflow: WorkflowEnvelope, context: WorkflowContext) -> None:
        raise RuntimeError("workflow failed")

    consumer = WorkflowConsumer(
        rabbitmq=rabbit,
        handler=handler,
        stage="docsearch_planner",
        retry_policy=RetryPolicy(max_retries=3, delay_ms=1000),
        retry_statuses=RetryStatusConfig(enabled=True),
        observation_sink=sink,
    )

    await run_consumer_until_message_done(consumer, message)

    assert message.acked is True
    assert rabbit.raw_queue_publishes[0]["queue_name"] == "cq.docsearch_planner.in_queue.retry"
    assert rabbit.published_statuses[0]["status"] == "retrying"
    stage_failed = next(event for event in observations if isinstance(event, WorkflowStageFailed))
    assert stage_failed.exception_message == "workflow failed"


@pytest.mark.asyncio
async def test_workflow_consumer_rejects_unsupported_action_when_retry_enabled() -> None:
    topology = SharedStatusWorkflowTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        workflow_exchange="workflow.exchange",
        status_exchange="status.exchange",
        status_queue="status.queue",
        stages=(
            WorkflowStage(
                name="docsearch_planner",
                queue="cq.docsearch_planner.in_queue",
                binding_keys=("planner.docsearch_planner.in",),
                publish_routing_key="planner.docsearch_planner.in",
                accepted_actions=(ActionSchema(action="collect"),),
            ),
        ),
    )
    message = FakeMessage(
        json.dumps(
            {
                "task_id": "task-123",
                "message_id": "msg-123",
                "stage": "docsearch_planner",
                "origin_stage": "topic_planner",
                "action": "review",
                "payload": {"docs": ["a"]},
            }
        ).encode("utf-8"),
        correlation_id="corr-123",
    )
    queue = FakeQueue([message])
    channel = FakeChannel(queue)
    rabbit = FakeRabbitClient(topology=topology, acquire_results=[channel])
    handler_calls = 0

    async def handler(workflow: WorkflowEnvelope, context: WorkflowContext) -> None:
        nonlocal handler_calls
        handler_calls += 1

    consumer = WorkflowConsumer(
        rabbitmq=rabbit,
        handler=handler,
        stage="docsearch_planner",
        retry_policy=RetryPolicy(max_retries=2, delay_ms=1000),
    )

    await run_consumer_until_message_done(consumer, message)

    assert message.acked is True
    assert handler_calls == 0
    assert rabbit.raw_queue_publishes[0]["headers"]["x-relayna-failure-reason"] == "unsupported_action"


@pytest.mark.asyncio
async def test_workflow_consumer_rejects_payload_schema_violations_when_retry_enabled() -> None:
    topology = SharedStatusWorkflowTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        workflow_exchange="workflow.exchange",
        status_exchange="status.exchange",
        status_queue="status.queue",
        stages=(
            WorkflowStage(
                name="docsearch_planner",
                queue="cq.docsearch_planner.in_queue",
                binding_keys=("planner.docsearch_planner.in",),
                publish_routing_key="planner.docsearch_planner.in",
                accepted_actions=(
                    ActionSchema(
                        action="collect",
                        payload=PayloadSchema(name="collect_payload", required_fields=("query",)),
                    ),
                ),
            ),
        ),
    )
    message = FakeMessage(
        json.dumps(
            {
                "task_id": "task-123",
                "message_id": "msg-123",
                "stage": "docsearch_planner",
                "origin_stage": "topic_planner",
                "action": "collect",
                "payload": {"docs": ["a"]},
            }
        ).encode("utf-8"),
        correlation_id="corr-123",
    )
    queue = FakeQueue([message])
    channel = FakeChannel(queue)
    rabbit = FakeRabbitClient(topology=topology, acquire_results=[channel])

    async def handler(workflow: WorkflowEnvelope, context: WorkflowContext) -> None:
        raise AssertionError("handler should not run")

    consumer = WorkflowConsumer(
        rabbitmq=rabbit,
        handler=handler,
        stage="docsearch_planner",
        retry_policy=RetryPolicy(max_retries=2, delay_ms=1000),
    )

    await run_consumer_until_message_done(consumer, message)

    assert message.acked is True
    assert rabbit.raw_queue_publishes[0]["headers"]["x-relayna-failure-reason"] == "payload_schema_violation"


@pytest.mark.asyncio
async def test_workflow_consumer_uses_stage_timeout_reason_for_retries() -> None:
    topology = SharedStatusWorkflowTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        workflow_exchange="workflow.exchange",
        status_exchange="status.exchange",
        status_queue="status.queue",
        stages=(
            WorkflowStage(
                name="docsearch_planner",
                queue="cq.docsearch_planner.in_queue",
                binding_keys=("planner.docsearch_planner.in",),
                publish_routing_key="planner.docsearch_planner.in",
                timeout_seconds=0.01,
            ),
        ),
    )
    message = FakeMessage(
        json.dumps(
            {
                "task_id": "task-123",
                "message_id": "msg-123",
                "stage": "docsearch_planner",
                "origin_stage": "topic_planner",
                "payload": {"docs": ["a"]},
            }
        ).encode("utf-8"),
        correlation_id="corr-123",
    )
    queue = FakeQueue([message])
    channel = FakeChannel(queue)
    rabbit = FakeRabbitClient(topology=topology, acquire_results=[channel])

    async def handler(workflow: WorkflowEnvelope, context: WorkflowContext) -> None:
        await asyncio.sleep(0.05)

    consumer = WorkflowConsumer(
        rabbitmq=rabbit,
        handler=handler,
        stage="docsearch_planner",
        retry_policy=RetryPolicy(max_retries=2, delay_ms=1000),
    )

    await run_consumer_until_message_done(consumer, message)

    assert message.acked is True
    assert rabbit.raw_queue_publishes[0]["headers"]["x-relayna-failure-reason"] == "stage_timeout"


@pytest.mark.asyncio
async def test_workflow_consumer_stage_retry_settings_override_consumer_defaults() -> None:
    topology = SharedStatusWorkflowTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        workflow_exchange="workflow.exchange",
        status_exchange="status.exchange",
        status_queue="status.queue",
        stages=(
            WorkflowStage(
                name="docsearch_planner",
                queue="cq.docsearch_planner.in_queue",
                binding_keys=("planner.docsearch_planner.in",),
                publish_routing_key="planner.docsearch_planner.in",
                max_retries=1,
                retry_delay_ms=250,
            ),
        ),
    )
    message = FakeMessage(
        json.dumps(
            {
                "task_id": "task-123",
                "message_id": "msg-123",
                "stage": "docsearch_planner",
                "origin_stage": "topic_planner",
                "payload": {"docs": ["a"]},
            }
        ).encode("utf-8"),
        correlation_id="corr-123",
    )
    queue = FakeQueue([message])
    channel = FakeChannel(queue)
    rabbit = FakeRabbitClient(topology=topology, acquire_results=[channel])

    async def handler(workflow: WorkflowEnvelope, context: WorkflowContext) -> None:
        raise RuntimeError("boom")

    consumer = WorkflowConsumer(
        rabbitmq=rabbit,
        handler=handler,
        stage="docsearch_planner",
        retry_policy=RetryPolicy(max_retries=5, delay_ms=1000),
    )

    await run_consumer_until_message_done(consumer, message)

    assert rabbit.retry_infrastructure_calls[0]["delay_ms"] == 250
    assert rabbit.raw_queue_publishes[0]["headers"]["x-relayna-max-retries"] == 1


@pytest.mark.asyncio
async def test_workflow_consumer_uses_stage_max_inflight_for_prefetch() -> None:
    topology = SharedStatusWorkflowTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        workflow_exchange="workflow.exchange",
        status_exchange="status.exchange",
        status_queue="status.queue",
        stages=(
            WorkflowStage(
                name="docsearch_planner",
                queue="cq.docsearch_planner.in_queue",
                binding_keys=("planner.docsearch_planner.in",),
                publish_routing_key="planner.docsearch_planner.in",
                max_inflight=7,
            ),
        ),
    )
    message = FakeMessage(
        json.dumps(
            {
                "task_id": "task-123",
                "message_id": "msg-123",
                "stage": "docsearch_planner",
                "origin_stage": "topic_planner",
                "payload": {"docs": ["a"]},
            }
        ).encode("utf-8"),
        correlation_id="corr-123",
    )
    queue = FakeQueue([message])
    channel = FakeChannel(queue)
    rabbit = FakeRabbitClient(topology=topology, acquire_results=[channel])

    async def handler(workflow: WorkflowEnvelope, context: WorkflowContext) -> None:
        return None

    consumer = WorkflowConsumer(
        rabbitmq=rabbit,
        handler=handler,
        stage="docsearch_planner",
    )

    await run_consumer_until_message_done(consumer, message)

    assert rabbit.acquire_channel_calls[0] == 7


@pytest.mark.asyncio
async def test_workflow_consumer_requires_contract_store_for_dedup_stage() -> None:
    topology = SharedStatusWorkflowTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        workflow_exchange="workflow.exchange",
        status_exchange="status.exchange",
        status_queue="status.queue",
        stages=(
            WorkflowStage(
                name="docsearch_planner",
                queue="cq.docsearch_planner.in_queue",
                binding_keys=("planner.docsearch_planner.in",),
                publish_routing_key="planner.docsearch_planner.in",
                dedup_key_fields=("doc_id",),
            ),
        ),
    )
    rabbit = FakeRabbitClient(topology=topology, acquire_results=[])

    async def handler(workflow: WorkflowEnvelope, context: WorkflowContext) -> None:
        return None

    consumer = WorkflowConsumer(
        rabbitmq=rabbit,
        handler=handler,
        stage="docsearch_planner",
    )

    with pytest.raises(RuntimeError, match="requires contract_store"):
        await consumer.run_forever()


@pytest.mark.asyncio
async def test_workflow_consumer_short_circuits_on_dedup_conflict() -> None:
    topology = SharedStatusWorkflowTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        workflow_exchange="workflow.exchange",
        status_exchange="status.exchange",
        status_queue="status.queue",
        stages=(
            WorkflowStage(
                name="docsearch_planner",
                queue="cq.docsearch_planner.in_queue",
                binding_keys=("planner.docsearch_planner.in",),
                publish_routing_key="planner.docsearch_planner.in",
                dedup_key_fields=("doc_id",),
            ),
        ),
    )
    message = FakeMessage(
        json.dumps(
            {
                "task_id": "task-123",
                "message_id": "msg-123",
                "stage": "docsearch_planner",
                "origin_stage": "topic_planner",
                "action": "collect",
                "payload": {"doc_id": "abc"},
            }
        ).encode("utf-8"),
        correlation_id="corr-123",
    )
    queue = FakeQueue([message])
    channel = FakeChannel(queue)
    rabbit = FakeRabbitClient(topology=topology, acquire_results=[channel])
    contract_store = FakeContractStore(acquire_result=False)
    handler_calls = 0

    async def handler(workflow: WorkflowEnvelope, context: WorkflowContext) -> None:
        nonlocal handler_calls
        handler_calls += 1

    consumer = WorkflowConsumer(
        rabbitmq=rabbit,
        handler=handler,
        stage="docsearch_planner",
        retry_policy=RetryPolicy(max_retries=2, delay_ms=1000),
        contract_store=contract_store,
    )

    await run_consumer_until_message_done(consumer, message)

    assert message.acked is True
    assert handler_calls == 0
    assert rabbit.raw_queue_publishes[0]["headers"]["x-relayna-failure-reason"] == "dedup_conflict"
    assert contract_store.acquire_calls
    assert not contract_store.inflight_mark_calls


@pytest.mark.asyncio
async def test_workflow_consumer_acks_when_dedup_cleanup_fails() -> None:
    topology = SharedStatusWorkflowTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        workflow_exchange="workflow.exchange",
        status_exchange="status.exchange",
        status_queue="status.queue",
        stages=(
            WorkflowStage(
                name="docsearch_planner",
                queue="cq.docsearch_planner.in_queue",
                binding_keys=("planner.docsearch_planner.in",),
                publish_routing_key="planner.docsearch_planner.in",
                dedup_key_fields=("doc_id",),
            ),
        ),
    )
    message = FakeMessage(
        json.dumps(
            {
                "task_id": "task-123",
                "message_id": "msg-123",
                "stage": "docsearch_planner",
                "origin_stage": "topic_planner",
                "action": "collect",
                "payload": {"doc_id": "abc"},
            }
        ).encode("utf-8"),
        correlation_id="corr-123",
    )
    queue = FakeQueue([message])
    channel = FakeChannel(queue)
    rabbit = FakeRabbitClient(topology=topology, acquire_results=[channel])
    contract_store = FakeContractStore(fail_clear_inflight=True)

    async def handler(workflow: WorkflowEnvelope, context: WorkflowContext) -> None:
        return None

    consumer = WorkflowConsumer(
        rabbitmq=rabbit,
        handler=handler,
        stage="docsearch_planner",
        contract_store=contract_store,
    )

    await run_consumer_until_message_done(consumer, message)

    assert message.acked is True
    assert not rabbit.raw_queue_publishes
    assert contract_store.acquire_calls
    assert contract_store.release_calls == [{"stage": "docsearch_planner", "task_id": "task-123", "action": "collect"}]
