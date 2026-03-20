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
)
from relayna.contracts import StatusEventEnvelope
from relayna.observability import (
    ConsumerDeadLetterPublished,
    ConsumerRetryScheduled,
    TaskConsumerLoopError,
    TaskConsumerStarted,
    TaskHandlerFailed,
    TaskLifecycleStatusPublished,
    TaskMessageAcked,
    TaskMessageReceived,
    TaskMessageRejected,
)
from relayna.rabbitmq import RetryInfrastructure
from relayna.topology import SharedTasksSharedStatusTopology


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
        self.aggregation_queue_calls: list[dict[str, Any]] = []
        self.retry_infrastructure_calls: list[dict[str, Any]] = []
        self.raw_queue_publishes: list[dict[str, Any]] = []

    async def ensure_tasks_queue(self) -> str:
        self.ensure_tasks_queue_calls += 1
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
            self.published_statuses.append(event.model_dump(mode="json", exclude_none=True))
        else:
            self.published_statuses.append(dict(event))

    async def publish_aggregation_status(self, event: StatusEventEnvelope | dict[str, Any]) -> None:
        await self.publish_status(event)

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


def make_topology() -> SharedTasksSharedStatusTopology:
    return SharedTasksSharedStatusTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.queue",
        tasks_routing_key="task.request",
        status_exchange="status.exchange",
        status_queue="status.queue",
    )


async def run_consumer_until_message_done(consumer: TaskConsumer | AggregationConsumer, message: FakeMessage) -> None:
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
    assert channel.declare_queue_calls == [
        {"name": "tasks.queue", "durable": True, "arguments": None}
    ]
    assert channel.close_calls >= 1


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

    monkeypatch.setattr("relayna.consumer.asyncio.sleep", fake_sleep)
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
    assert isinstance(observed[2], TaskLifecycleStatusPublished)
    assert observed[2].status == "processing"
    assert isinstance(observed[3], TaskLifecycleStatusPublished)
    assert observed[3].status == "completed"
    assert isinstance(observed[4], TaskMessageAcked)


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
    assert any(isinstance(event, TaskHandlerFailed) for event in failed_observed)
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

    monkeypatch.setattr("relayna.consumer.asyncio.sleep", fake_sleep)
    consumer = TaskConsumer(rabbitmq=rabbit, handler=handler, idle_retry_seconds=0.25, observation_sink=sink)

    await run_consumer_until_message_done(consumer, message)

    loop_errors = [event for event in observed if isinstance(event, TaskConsumerLoopError)]
    assert loop_errors
    assert loop_errors[0].retry_delay_seconds == 0.25


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

    monkeypatch.setattr("relayna.consumer.asyncio.wait_for", fake_wait_for)

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
    assert rabbit.raw_queue_publishes[0]["queue_name"] == "aggregation.queue.0.retry"
    assert rabbit.published_statuses[0]["status"] == "retrying"
    assert rabbit.published_statuses[0]["meta"]["parent_task_id"] == "parent-1"


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


@pytest.mark.asyncio
async def test_aggregation_worker_runtime_requires_topology_or_rabbitmq() -> None:
    async def handler(task: Any, context: TaskContext) -> None:
        return None

    with pytest.raises(ValueError, match="rabbitmq=.*topology"):
        AggregationWorkerRuntime(handler=handler, shard_groups=[[0]])
