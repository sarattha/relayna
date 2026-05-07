from __future__ import annotations

from datetime import UTC, datetime

import pytest
from opentelemetry import context as otel_context

from relayna.observability import (
    RELAYNA_STUDIO_HIGH_CARDINALITY_BODY_FIELDS,
    RELAYNA_STUDIO_LOKI_LABEL_ALLOWLIST,
    AggregationHandlerFailed,
    ConsumerDLQRecordPersistFailed,
    RedisObservationStore,
    SSEKeepaliveSent,
    StatusHubLoopError,
    StatusHubStoreWriteFailed,
    TaskConsumerLoopError,
    TaskHandlerFailed,
    TaskMessageReceived,
    WorkflowStageFailed,
    active_trace_fields,
    bind_studio_log_context,
    emit_observation,
    extract_trace_context,
    inject_trace_headers,
    make_redis_observation_sink,
    make_structlog_observation_sink,
    observation_to_studio_log_fields,
    validate_studio_log_fields,
)


class FakePipeline:
    def __init__(self, redis: FakeRedis) -> None:
        self._redis = redis
        self._ops: list[tuple[str, tuple[object, ...]]] = []

    def lpush(self, key: str, payload: str) -> None:
        self._ops.append(("lpush", (key, payload)))

    def ltrim(self, key: str, start: int, stop: int) -> None:
        self._ops.append(("ltrim", (key, start, stop)))

    def expire(self, key: str, ttl: int) -> None:
        self._ops.append(("expire", (key, ttl)))

    async def execute(self) -> list[object]:
        results: list[object] = []
        for op, args in self._ops:
            if op == "lpush":
                key, payload = args
                self._redis.history.setdefault(str(key), []).insert(0, str(payload))
                results.append(1)
            elif op == "ltrim":
                key, start, stop = args
                items = self._redis.history.get(str(key), [])
                self._redis.history[str(key)] = items[int(start) : int(stop) + 1]
                results.append(True)
            elif op == "expire":
                key, ttl = args
                self._redis.expirations[str(key)] = int(ttl)
                results.append(True)
        return results


class FakeRedis:
    def __init__(self) -> None:
        self.history: dict[str, list[str]] = {}
        self.expirations: dict[str, int] = {}
        self._seen_keys: set[str] = set()

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool:
        assert value == "1"
        if nx and key in self._seen_keys:
            return False
        self._seen_keys.add(key)
        if ex is not None:
            self.expirations[key] = ex
        return True

    def pipeline(self) -> FakePipeline:
        return FakePipeline(self)

    async def lrange(self, key: str, start: int, stop: int) -> list[str]:
        items = self.history.get(key, [])
        return items[int(start) : int(stop) + 1]


class FakeStructlogLogger:
    def __init__(self, context: dict[str, object] | None = None, calls: list[dict[str, object]] | None = None) -> None:
        self.context = context or {}
        self.calls = calls if calls is not None else []

    def bind(self, **kwargs: object) -> FakeStructlogLogger:
        return FakeStructlogLogger({**self.context, **kwargs}, self.calls)

    def info(self, event: str, **kwargs: object) -> None:
        self.calls.append({"method": "info", "event": event, **self.context, **kwargs})

    def warning(self, event: str, **kwargs: object) -> None:
        self.calls.append({"method": "warning", "event": event, **self.context, **kwargs})

    def error(self, event: str, **kwargs: object) -> None:
        self.calls.append({"method": "error", "event": event, **self.context, **kwargs})


@pytest.mark.asyncio
async def test_emit_observation_is_noop_when_sink_is_none() -> None:
    await emit_observation(None, SSEKeepaliveSent(task_id="task-123"))


@pytest.mark.asyncio
async def test_emit_observation_suppresses_sink_failures() -> None:
    async def sink(event: object) -> None:
        raise RuntimeError("sink failed")

    await emit_observation(sink, SSEKeepaliveSent(task_id="task-123"))


@pytest.mark.asyncio
async def test_redis_observation_store_skips_events_without_task_id() -> None:
    redis = FakeRedis()
    store = RedisObservationStore(redis, prefix="obs", ttl_seconds=60, history_maxlen=10)

    stored = await store.set_event(object())

    assert stored is False
    assert redis.history == {}


def test_redis_observation_store_uses_runtime_default_prefix() -> None:
    redis = FakeRedis()
    store = RedisObservationStore(redis)

    assert store.prefix == "relayna-observations"


@pytest.mark.asyncio
async def test_make_redis_observation_sink_persists_and_dedupes_events() -> None:
    redis = FakeRedis()
    store = RedisObservationStore(redis, prefix="obs", ttl_seconds=60, history_maxlen=10)
    sink = make_redis_observation_sink(store)
    event = SSEKeepaliveSent(task_id="task-123")

    await sink(event)
    await sink(event)

    history = await store.get_history("task-123")

    assert len(history) == 1
    assert history[0]["event_type"] == "SSEKeepaliveSent"
    assert redis.expirations[store.history_key("task-123")] == 60


def test_studio_log_contract_converts_task_observation_to_structlog_fields() -> None:
    fields = observation_to_studio_log_fields(
        TaskMessageReceived(
            consumer_name="payments-worker",
            queue_name="payments.tasks",
            task_id="task-123",
            correlation_id="corr-123",
            retry_attempt=2,
            task_type="charge",
        ),
        service="payments",
        app="payments-worker",
        env="prod",
        runtime="worker",
        stage="charge",
    )

    assert validate_studio_log_fields(fields, require_task_context=True) == set()
    assert fields["service"] == "payments"
    assert fields["app"] == "payments-worker"
    assert fields["event"] == "task_message_received"
    assert fields["level"] == "info"
    assert fields["task_id"] == "task-123"
    assert fields["correlation_id"] == "corr-123"
    assert fields["stage"] == "charge"
    assert fields["attempt"] == 2
    assert "retry_attempt" not in fields
    assert str(fields["timestamp"]).endswith("Z")


def test_studio_log_contract_marks_failure_observations_as_error() -> None:
    fields = observation_to_studio_log_fields(
        TaskHandlerFailed(
            consumer_name="payments-worker",
            queue_name="payments.tasks",
            task_id="task-123",
            correlation_id="corr-123",
            retry_attempt=1,
            exception_type="ValueError",
            exception_message="bad amount",
        ),
        service="payments",
        app="payments-worker",
        stage="charge",
    )

    assert fields["event"] == "task_handler_failed"
    assert fields["level"] == "error"
    assert fields["attempt"] == 1
    assert fields["exception_message"] == "bad amount"


def test_failure_observation_events_include_exception_message() -> None:
    events = [
        TaskHandlerFailed("worker", "tasks", "task-123", exception_type="RuntimeError"),
        AggregationHandlerFailed("worker", "aggregations", "task-123", None, exception_type="RuntimeError"),
        WorkflowStageFailed(
            "worker", "stage.queue", "stage-a", None, "task-123", None, None, None, "RuntimeError", False
        ),
        ConsumerDLQRecordPersistFailed("worker", "task-123", "tasks.dlq", "tasks", exception_type="RuntimeError"),
        TaskConsumerLoopError("worker", "RuntimeError", 2.0),
        StatusHubStoreWriteFailed("task-123", "RuntimeError"),
        StatusHubLoopError("RuntimeError", 2.0),
    ]

    for event in events:
        assert event.exception_message == ""


def test_failure_observation_events_preserve_positional_constructor_order() -> None:
    task_failed = TaskHandlerFailed("worker", "tasks", "task-123", "RuntimeError", True)
    assert task_failed.exception_type == "RuntimeError"
    assert task_failed.requeue is True
    assert task_failed.exception_message == ""

    aggregation_failed = AggregationHandlerFailed(
        "worker", "aggregations", "task-123", "parent-123", "corr-123", 1, "RuntimeError", True
    )
    assert aggregation_failed.exception_type == "RuntimeError"
    assert aggregation_failed.requeue is True
    assert aggregation_failed.exception_message == ""

    workflow_failed = WorkflowStageFailed(
        "worker",
        "stage.queue",
        "stage-a",
        None,
        "task-123",
        None,
        None,
        None,
        "RuntimeError",
        True,
    )
    assert workflow_failed.exception_type == "RuntimeError"
    assert workflow_failed.requeue is True
    assert workflow_failed.exception_message == ""

    dlq_failed = ConsumerDLQRecordPersistFailed(
        "worker", "task-123", "tasks.dlq", "tasks", 1, 2, "handler_error", "RuntimeError"
    )
    assert dlq_failed.exception_type == "RuntimeError"
    assert dlq_failed.exception_message == ""

    loop_error = TaskConsumerLoopError("worker", "RuntimeError", 2.0)
    assert loop_error.exception_type == "RuntimeError"
    assert loop_error.retry_delay_seconds == 2.0
    assert loop_error.exception_message == ""

    store_failed = StatusHubStoreWriteFailed("task-123", "RuntimeError")
    assert store_failed.exception_type == "RuntimeError"
    assert store_failed.exception_message == ""

    status_loop_error = StatusHubLoopError("RuntimeError", 2.0)
    assert status_loop_error.exception_type == "RuntimeError"
    assert status_loop_error.retry_delay_seconds == 2.0
    assert status_loop_error.exception_message == ""


def test_studio_log_contract_keeps_canonical_fields_when_mapping_contains_reserved_keys() -> None:
    fields = observation_to_studio_log_fields(
        {
            "event": "TaskHandlerFailed",
            "event_type": "TaskMessageReceived",
            "level": "debug",
            "timestamp": datetime(2026, 5, 1, 12, 30, tzinfo=UTC),
            "task_id": "task-123",
            "correlation_id": "corr-123",
            "retry_attempt": 3,
        },
        service="payments",
        app="payments-worker",
        stage="charge",
        level="debug",
        timestamp="caller_override",
    )

    assert fields["event"] == "task_handler_failed"
    assert fields["level"] == "error"
    assert fields["timestamp"] == "2026-05-01T12:30:00Z"
    assert fields["attempt"] == 3
    assert fields["task_id"] == "task-123"
    assert fields["correlation_id"] == "corr-123"


def test_studio_loki_label_contract_keeps_task_identifiers_in_body() -> None:
    assert {"service", "app", "level", "stage"}.issubset(RELAYNA_STUDIO_LOKI_LABEL_ALLOWLIST)
    assert {"task_id", "correlation_id", "request_id", "pod", "worker_id"}.issubset(
        RELAYNA_STUDIO_HIGH_CARDINALITY_BODY_FIELDS
    )
    assert RELAYNA_STUDIO_LOKI_LABEL_ALLOWLIST.isdisjoint(RELAYNA_STUDIO_HIGH_CARDINALITY_BODY_FIELDS)


def test_trace_helpers_are_noop_without_active_trace() -> None:
    headers = inject_trace_headers({"task_id": "task-123"})

    assert headers == {"task_id": "task-123"}
    assert active_trace_fields() == {}


def test_trace_helpers_extract_w3c_trace_context() -> None:
    traceparent = "00-11111111111111111111111111111111-2222222222222222-01"
    token = otel_context.attach(extract_trace_context({"traceparent": traceparent}))
    try:
        assert active_trace_fields() == {
            "trace_id": "11111111111111111111111111111111",
            "span_id": "2222222222222222",
        }
        assert inject_trace_headers()["traceparent"].startswith("00-11111111111111111111111111111111-")
    finally:
        otel_context.detach(token)


@pytest.mark.asyncio
async def test_make_structlog_observation_sink_emits_structlog_style_event() -> None:
    logger = bind_studio_log_context(
        FakeStructlogLogger(),
        service="payments",
        app="payments-worker",
        env="prod",
        runtime="worker",
    )
    sink = make_structlog_observation_sink(logger, service="payments", app="payments-worker")

    await sink(
        TaskHandlerFailed(
            consumer_name="payments-worker",
            queue_name="payments.tasks",
            task_id="task-123",
            correlation_id="corr-123",
            retry_attempt=1,
            exception_type="ValueError",
            exception_message="bad amount",
        )
    )

    assert len(logger.calls) == 1
    call = logger.calls[0]
    assert call["method"] == "error"
    assert call["event"] == "task_handler_failed"
    assert call["service"] == "payments"
    assert call["app"] == "payments-worker"
    assert call["env"] == "prod"
    assert call["runtime"] == "worker"
    assert call["level"] == "error"
    assert call["consumer_name"] == "payments-worker"
    assert call["queue_name"] == "payments.tasks"
    assert call["task_id"] == "task-123"
    assert call["correlation_id"] == "corr-123"
    assert call["attempt"] == 1
    assert call["exception_type"] == "ValueError"
    assert call["exception_message"] == "bad amount"
    assert call["requeue"] is False
    assert call["component"] == "consumer"
    assert str(call["timestamp"]).endswith("Z")
    assert "task_type" not in call
