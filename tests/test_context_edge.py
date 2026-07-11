from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from relayna.consumer.context import (
    TaskContext,
    WorkflowContext,
    _coerce_non_negative_int,
    _coerce_task_id,
    _failure_message,
    _header_int,
    _manual_retry_context_meta,
    _manual_retry_meta_from_status,
    _ManualRetryRequested,
    _message_headers,
    _normalize_batch_payload,
    _normalize_payload,
    _persist_dlq_record,
    _resolve_workflow_stage_for_routing_key,
    _retry_attempt,
    _string_or_none,
    _topic_binding_matches,
)
from relayna.contracts import ContractAliasConfig, StatusEventEnvelope
from relayna.topology import SharedStatusWorkflowTopology, WorkflowEntryRoute, WorkflowStage


class Rabbit:
    def __init__(self) -> None:
        self.statuses: list[Any] = []
        self.aggregation_statuses: list[Any] = []

    async def publish_status(self, event: Any) -> None:
        self.statuses.append(event)

    async def publish_aggregation_status(self, event: Any) -> None:
        self.aggregation_statuses.append(event)


def task_context(*, raw_payload: dict[str, Any] | None = None, is_task_context: bool = True) -> TaskContext:
    return TaskContext(
        rabbitmq=Rabbit(),  # type: ignore[arg-type]
        consumer_name="consumer",
        raw_payload=raw_payload or {"task_id": "task", "task_type": "type.a", "payload": {"one": 1}},
        correlation_id="corr",
        delivery_tag=1,
        redelivered=False,
        _task_id="task",
        is_task_context=is_task_context,
    )


@pytest.mark.asyncio
async def test_context_publish_validation_and_manual_retry_validation_paths() -> None:
    context = task_context()
    event = StatusEventEnvelope(task_id="other", status="done")
    with pytest.raises(ValueError, match="either"):
        await context.publish_status(event, status="done")
    with pytest.raises(ValueError, match="status is required"):
        await context.publish_status()
    with pytest.raises(ValueError, match="either"):
        await context.publish_aggregation_status(event, status="done")
    with pytest.raises(ValueError, match="status is required"):
        await context.publish_aggregation_status()
    await context.publish_status(event)
    await context.publish_aggregation_status(event)
    assert context.rabbitmq.statuses[0].task_id == "task"  # type: ignore[attr-defined]
    assert context.rabbitmq.aggregation_statuses[0].task_id == "task"  # type: ignore[attr-defined]

    with pytest.raises(ValueError, match="either payload"):
        await context.manual_retry(payload={}, payload_merge={})
    with pytest.raises(ValueError, match="task envelope"):
        await task_context(is_task_context=False).manual_retry()
    with pytest.raises(ValueError, match="task envelope"):
        await task_context(raw_payload={"invalid": True}).manual_retry()
    with pytest.raises(ValueError, match="task_type must not be empty"):
        await context.manual_retry(task_type=" ")
    with pytest.raises(ValueError, match="reserved"):
        await context.manual_retry(extra_fields={"task_id": "other"})
    with pytest.raises(_ManualRetryRequested):
        await context.manual_retry(
            task_type="type.b",
            service="service",
            payload={"replacement": True},
            priority=3,
            extra_fields={"custom": True},
        )
    assert context._manual_retry_request is not None
    assert context._manual_retry_request.task["task_type"] == "type.b"


@pytest.mark.asyncio
async def test_workflow_context_status_validation_paths() -> None:
    rabbit = Rabbit()
    context = WorkflowContext(
        rabbitmq=rabbit,  # type: ignore[arg-type]
        consumer_name="consumer",
        stage="planner",
        raw_payload={},
        correlation_id="corr",
        delivery_tag=1,
        redelivered=False,
        _task_id="task",
        _message_id="message",
    )
    event = StatusEventEnvelope(task_id="other", status="done")
    with pytest.raises(ValueError, match="either"):
        await context.publish_status(event, status="done")
    with pytest.raises(ValueError, match="status is required"):
        await context.publish_status()
    await context.publish_status(event, meta={"override": True})
    assert rabbit.statuses[0].meta == {"override": True}


def test_context_private_normalization_routing_and_header_helpers() -> None:
    topology = SharedStatusWorkflowTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        workflow_exchange="workflow.exchange",
        status_exchange="status.exchange",
        status_queue="status.queue",
        stages=(
            WorkflowStage(
                name="planner",
                queue="planner.queue",
                binding_keys=("planner.*", "fallback.#"),
                publish_routing_key="planner.out",
            ),
        ),
        entry_routes=(WorkflowEntryRoute("entry", "entry.in", "planner"),),
    )
    assert _resolve_workflow_stage_for_routing_key(topology, "planner.out") == "planner"
    assert _resolve_workflow_stage_for_routing_key(topology, "planner.x") == "planner"
    assert _resolve_workflow_stage_for_routing_key(topology, "entry.in") == "planner"
    assert _resolve_workflow_stage_for_routing_key(topology, "fallback.a.b") == "planner"
    with pytest.raises(KeyError):
        _resolve_workflow_stage_for_routing_key(topology, "missing")
    assert _topic_binding_matches("a.#", "a") is True
    assert _topic_binding_matches("a.*", "a") is False
    assert _topic_binding_matches("a.b", "a.c") is False

    assert _coerce_task_id({"task_id": "task"}) == "task"
    assert _coerce_task_id({"task_id": 1}) is None
    assert _coerce_task_id([]) is None
    assert _message_headers(SimpleNamespace(headers={"a": 1})) == {"a": 1}
    assert _message_headers(SimpleNamespace(headers=[])) == {}
    assert _retry_attempt(SimpleNamespace(headers={"x-relayna-retry-attempt": "bad"})) == 0
    assert _header_int({"value": "bad"}, "value") is None
    assert _string_or_none(None) is None
    assert _string_or_none(" ") is None
    assert _manual_retry_meta_from_status(None)["count"] == 0
    assert _manual_retry_meta_from_status({})["count"] == 0
    assert _coerce_non_negative_int("bad", fallback=-1) == 0
    assert _failure_message(RuntimeError(" "), include_error_message=True) == "Task processing failed."
    assert _normalize_payload([], alias_config=None) == []
    aliases = ContractAliasConfig(field_aliases={"task_id": "attempt_id"})
    assert (
        _normalize_batch_payload(
            {"batch_id": "batch", "tasks": [{"attempt_id": "task"}]},
            alias_config=aliases,
        )["tasks"][0]["task_id"]
        == "task"
    )
    assert _manual_retry_context_meta(task_context()) == {}


@pytest.mark.asyncio
async def test_persist_dlq_record_none_and_cancellation_paths() -> None:
    kwargs = {
        "consumer_name": "consumer",
        "queue_name": "queue.dlq",
        "source_queue_name": "queue",
        "retry_queue_name": "queue.retry",
        "task_id": "task",
        "correlation_id": "corr",
        "reason": "failed",
        "exception_type": "RuntimeError",
        "retry_attempt": 1,
        "max_retries": 1,
        "headers": {},
        "content_type": "application/json",
        "body": b"{}",
    }
    await _persist_dlq_record(None, **kwargs)

    class Store:
        async def add(self, record: Any) -> None:
            raise __import__("asyncio").CancelledError

    with pytest.raises(__import__("asyncio").CancelledError):
        await _persist_dlq_record(Store(), **kwargs)
