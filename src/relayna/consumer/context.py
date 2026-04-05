from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from functools import cache
from typing import Any, Protocol

from pydantic import ValidationError

from ..contracts import (
    ContractAliasConfig,
    StatusEventEnvelope,
    TaskEnvelope,
    WorkflowEnvelope,
    normalize_contract_aliases,
)
from ..dlq import DLQRecorder, build_dlq_record
from ..observability import (
    ConsumerDLQRecordPersistFailed,
    ObservationSink,
    WorkflowMessagePublished,
    emit_observation,
)
from ..rabbitmq import RelaynaRabbitClient
from ..topology import RelaynaTopology, SharedStatusWorkflowTopology


class TaskHandler(Protocol):
    async def __call__(self, task: TaskEnvelope, context: TaskContext) -> None: ...


class AggregationHandler(Protocol):
    async def __call__(self, event: StatusEventEnvelope, context: TaskContext) -> None: ...


class WorkflowHandler(Protocol):
    async def __call__(self, message: WorkflowEnvelope, context: WorkflowContext) -> None: ...


class FailureAction(str, Enum):
    REJECT = "reject"
    REQUEUE = "requeue"


@dataclass(slots=True)
class LifecycleStatusConfig:
    enabled: bool = False
    processing_status: str = "processing"
    completed_status: str = "completed"
    failed_status: str = "failed"
    include_error_message: bool = True


@dataclass(slots=True)
class RetryPolicy:
    max_retries: int = 3
    delay_ms: int = 30000
    retry_queue_suffix: str = ".retry"
    dead_letter_queue_suffix: str = ".dlq"


@dataclass(slots=True)
class RetryStatusConfig:
    enabled: bool = False
    retrying_status: str = "retrying"
    dead_lettered_status: str = "failed"
    include_error_message: bool = True


@dataclass(slots=True)
class _ManualRetryRequest:
    task: dict[str, Any]
    headers: dict[str, Any]
    status_message: str
    status_meta: dict[str, Any]


class _ManualRetryRequested(Exception):
    pass


@dataclass(slots=True)
class TaskContext:
    rabbitmq: RelaynaRabbitClient
    consumer_name: str
    raw_payload: dict[str, Any]
    correlation_id: str | None
    delivery_tag: int | None
    redelivered: bool
    _task_id: str = field(repr=False)
    retry_attempt: int = 0
    max_retries: int | None = None
    source_queue_name: str | None = None
    batch_id: str | None = None
    batch_index: int | None = None
    batch_size: int | None = None
    headers: dict[str, Any] = field(default_factory=dict)
    is_task_context: bool = True
    manual_retry_count: int = 0
    manual_retry_previous_task_type: str | None = None
    manual_retry_source_consumer: str | None = None
    manual_retry_reason: str | None = None
    _manual_retry_request: _ManualRetryRequest | None = field(default=None, init=False, repr=False)

    async def publish_status(
        self,
        event: StatusEventEnvelope | None = None,
        *,
        status: str | None = None,
        message: str | None = None,
        meta: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        event_id: str | None = None,
        service: str | None = None,
    ) -> None:
        if event is not None and status is not None:
            raise ValueError("Pass either a StatusEventEnvelope or keyword fields, not both.")
        if event is None and status is None:
            raise ValueError("status is required when event is not provided.")

        payload_meta = self._merge_status_meta(meta)
        if event is None:
            event = StatusEventEnvelope(
                task_id=self._task_id,
                status=status or "",
                message=message,
                meta=payload_meta,
                result=result,
                correlation_id=self.correlation_id,
                event_id=event_id,
                service=service,
            )
        else:
            event = event.model_copy(
                update={
                    "task_id": self._task_id,
                    "correlation_id": event.correlation_id or self.correlation_id,
                    "meta": self._merge_status_meta(event.meta),
                }
            )

        await self.rabbitmq.publish_status(event)

    async def publish_aggregation_status(
        self,
        event: StatusEventEnvelope | None = None,
        *,
        status: str | None = None,
        message: str | None = None,
        meta: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        event_id: str | None = None,
        service: str | None = None,
    ) -> None:
        if event is not None and status is not None:
            raise ValueError("Pass either a StatusEventEnvelope or keyword fields, not both.")
        if event is None and status is None:
            raise ValueError("status is required when event is not provided.")

        payload_meta = self._merge_status_meta(meta)
        if event is None:
            event = StatusEventEnvelope(
                task_id=self._task_id,
                status=status or "",
                message=message,
                meta=payload_meta,
                result=result,
                correlation_id=self.correlation_id,
                event_id=event_id,
                service=service,
            )
        else:
            event = event.model_copy(
                update={
                    "task_id": self._task_id,
                    "correlation_id": event.correlation_id or self.correlation_id,
                    "meta": {**payload_meta, **event.meta},
                }
            )

        await self.rabbitmq.publish_aggregation_status(event)

    async def manual_retry(
        self,
        *,
        task_type: str | None = None,
        payload: Mapping[str, Any] | None = None,
        payload_merge: Mapping[str, Any] | None = None,
        service: str | None = None,
        correlation_id: str | None = None,
        priority: int | None = None,
        reason: str | None = None,
        message: str | None = None,
        meta: Mapping[str, Any] | None = None,
        headers: Mapping[str, Any] | None = None,
        extra_fields: Mapping[str, Any] | None = None,
    ) -> None:
        if payload is not None and payload_merge is not None:
            raise ValueError("Pass either payload or payload_merge, not both.")
        if not self.is_task_context:
            raise ValueError("manual_retry requires a task envelope context")

        try:
            current = TaskEnvelope.model_validate(dict(self.raw_payload)).model_dump(mode="json", exclude_none=True)
        except ValidationError as exc:
            raise ValueError("manual_retry requires a task envelope context") from exc

        next_task = dict(current)
        next_task["task_id"] = self._task_id
        next_task["correlation_id"] = correlation_id or self.correlation_id or current.get("correlation_id")
        if priority is not None:
            next_task["priority"] = priority
        if task_type is not None:
            resolved_task_type = str(task_type).strip()
            if not resolved_task_type:
                raise ValueError("task_type must not be empty when provided")
            next_task["task_type"] = resolved_task_type
        if service is not None:
            next_task["service"] = service
        if payload is not None:
            next_task["payload"] = dict(payload)
        elif payload_merge is not None:
            merged_payload = dict(next_task.get("payload") or {})
            merged_payload.update(dict(payload_merge))
            next_task["payload"] = merged_payload
        if extra_fields is not None:
            provided_fields = dict(extra_fields)
            reserved_fields = {
                "task_id",
                "correlation_id",
                "task_type",
                "service",
                "payload",
                "priority",
                "spec_version",
                "created_at",
            }
            conflicting = sorted(key for key in provided_fields if key in reserved_fields)
            if conflicting:
                conflicts = ", ".join(conflicting)
                raise ValueError(f"extra_fields contains reserved key(s): {conflicts}")
            next_task.update(provided_fields)

        prepared = TaskEnvelope.model_validate(next_task).model_dump(mode="json", exclude_none=True)
        next_count = self.manual_retry_count + 1
        previous_task_type = str(current.get("task_type") or "").strip() or None
        target_task_type = str(prepared.get("task_type") or "").strip() or None

        self._manual_retry_request = _ManualRetryRequest(
            task=prepared,
            headers=_manual_retry_headers(
                self.headers,
                manual_retry_count=next_count,
                previous_task_type=previous_task_type,
                source_consumer=self.consumer_name,
                reason=reason,
                task_id=self._task_id,
                extra_headers=headers,
            ),
            status_message=message or reason or "Task handed off for manual retry.",
            status_meta=_manual_retry_status_meta(
                manual_retry_count=next_count,
                previous_task_type=previous_task_type,
                source_consumer=self.consumer_name,
                reason=reason,
                target_task_type=target_task_type,
                extra_meta=meta,
            ),
        )
        raise _ManualRetryRequested()

    def _merge_status_meta(self, meta: Mapping[str, Any] | None) -> dict[str, Any]:
        merged = dict(meta or {})
        lineage = _manual_retry_context_meta(self)
        if not lineage:
            return merged
        existing = merged.get("manual_retry")
        if isinstance(existing, Mapping):
            merged_manual_retry = {**lineage["manual_retry"], **dict(existing)}
            merged_manual_retry["count"] = _coerce_non_negative_int(
                merged_manual_retry.get("count"),
                fallback=int(lineage["manual_retry"].get("count", 0)),
            )
            merged["manual_retry"] = merged_manual_retry
            return merged
        merged.update(lineage)
        return merged


@dataclass(slots=True)
class WorkflowContext:
    rabbitmq: RelaynaRabbitClient
    consumer_name: str
    stage: str
    raw_payload: dict[str, Any]
    correlation_id: str | None
    delivery_tag: int | None
    redelivered: bool
    _task_id: str = field(repr=False)
    _message_id: str = field(repr=False)
    origin_stage: str | None = None
    retry_attempt: int = 0
    max_retries: int | None = None
    source_queue_name: str | None = None
    headers: dict[str, Any] = field(default_factory=dict)
    observation_sink: ObservationSink | None = None

    async def publish_status(
        self,
        event: StatusEventEnvelope | None = None,
        *,
        status: str | None = None,
        message: str | None = None,
        meta: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        event_id: str | None = None,
        service: str | None = None,
    ) -> None:
        if event is not None and status is not None:
            raise ValueError("Pass either a StatusEventEnvelope or keyword fields, not both.")
        if event is None and status is None:
            raise ValueError("status is required when event is not provided.")

        if event is None:
            event = StatusEventEnvelope(
                task_id=self._task_id,
                status=status or "",
                message=message,
                meta=dict(meta or {}),
                result=result,
                correlation_id=self.correlation_id,
                event_id=event_id,
                service=service,
            )
        else:
            event = event.model_copy(
                update={
                    "task_id": self._task_id,
                    "correlation_id": event.correlation_id or self.correlation_id,
                    "meta": dict(meta or event.meta),
                }
            )
        await self.rabbitmq.publish_status(event)

    async def publish_to_stage(
        self,
        stage: str,
        payload: Mapping[str, Any],
        *,
        action: str | None = None,
        meta: Mapping[str, Any] | None = None,
        priority: int | None = None,
    ) -> None:
        envelope = WorkflowEnvelope(
            task_id=self._task_id,
            correlation_id=self.correlation_id or self._task_id,
            stage=stage,
            origin_stage=self.stage,
            action=action,
            payload=dict(payload),
            meta=dict(meta or {}),
            priority=priority,
        )
        await self.rabbitmq.publish_to_stage(envelope, stage=stage)
        await emit_observation(
            self.observation_sink,
            WorkflowMessagePublished(
                consumer_name=self.consumer_name,
                queue_name=self.source_queue_name,
                stage=stage,
                routing_key=self.rabbitmq.topology.workflow_publish_routing_key(stage),
                task_id=self._task_id,
                message_id=envelope.message_id,
                origin_stage=self.stage,
                correlation_id=envelope.correlation_id,
            ),
        )

    async def publish_workflow_message(
        self,
        routing_key: str,
        payload: Mapping[str, Any],
        *,
        action: str | None = None,
        meta: Mapping[str, Any] | None = None,
        priority: int | None = None,
    ) -> None:
        destination_stage = _resolve_workflow_stage_for_routing_key(self.rabbitmq.topology, routing_key)
        envelope = WorkflowEnvelope(
            task_id=self._task_id,
            correlation_id=self.correlation_id or self._task_id,
            stage=destination_stage,
            origin_stage=self.stage,
            action=action,
            payload=dict(payload),
            meta=dict(meta or {}),
            priority=priority,
        )
        await self.rabbitmq.publish_workflow_message(envelope, routing_key=routing_key)
        await emit_observation(
            self.observation_sink,
            WorkflowMessagePublished(
                consumer_name=self.consumer_name,
                queue_name=self.source_queue_name,
                stage=destination_stage,
                routing_key=routing_key,
                task_id=self._task_id,
                message_id=envelope.message_id,
                origin_stage=self.stage,
                correlation_id=envelope.correlation_id,
            ),
        )


def _resolve_workflow_stage_for_routing_key(topology: RelaynaTopology, routing_key: str) -> str:
    for stage in topology.workflow_stage_names():
        if topology.workflow_publish_routing_key(stage) == routing_key:
            return stage
    for stage in topology.workflow_stage_names():
        if routing_key in topology.workflow_binding_keys(stage):
            return stage
    if isinstance(topology, SharedStatusWorkflowTopology):
        for route in topology.entry_routes:
            if route.routing_key == routing_key:
                return route.target_stage
    for stage in topology.workflow_stage_names():
        if any(
            _topic_binding_matches(binding_key, routing_key) for binding_key in topology.workflow_binding_keys(stage)
        ):
            return stage
    raise KeyError(f"No workflow stage publishes with routing key '{routing_key}'")


def _topic_binding_matches(binding_key: str, routing_key: str) -> bool:
    binding_parts = tuple(binding_key.split("."))
    routing_parts = tuple(routing_key.split("."))

    @cache
    def _matches(binding_index: int, routing_index: int) -> bool:
        if binding_index == len(binding_parts):
            return routing_index == len(routing_parts)

        token = binding_parts[binding_index]
        if token == "#":
            return _matches(binding_index + 1, routing_index) or (
                routing_index < len(routing_parts) and _matches(binding_index, routing_index + 1)
            )
        if routing_index == len(routing_parts):
            return False
        if token == "*" or token == routing_parts[routing_index]:
            return _matches(binding_index + 1, routing_index + 1)
        return False

    return _matches(0, 0)


def _coerce_task_id(payload: Any) -> str | None:
    if isinstance(payload, dict):
        task_id = payload.get("task_id")
        if isinstance(task_id, str):
            return task_id
    return None


def _message_headers(message: Any) -> dict[str, Any]:
    headers = getattr(message, "headers", None)
    if isinstance(headers, Mapping):
        return dict(headers)
    return {}


def _retry_attempt(message: Any) -> int:
    value = _message_headers(message).get("x-relayna-retry-attempt")
    if value is None:
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _retry_headers(
    message: Any,
    *,
    source_queue_name: str,
    retry_attempt: int,
    max_retries: int,
    reason: str,
    exception_type: str | None,
    extra_headers: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    headers = _message_headers(message)
    headers.update(dict(extra_headers or {}))
    headers["x-relayna-retry-attempt"] = int(retry_attempt)
    headers["x-relayna-max-retries"] = int(max_retries)
    headers["x-relayna-source-queue"] = source_queue_name
    headers["x-relayna-failure-reason"] = reason
    headers["x-relayna-exception-type"] = exception_type
    return headers


def _header_string(headers: Mapping[str, Any], key: str) -> str | None:
    value = headers.get(key)
    if isinstance(value, str):
        value = value.strip()
        if value:
            return value
    return None


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _header_int(headers: Mapping[str, Any], key: str) -> int | None:
    value = headers.get(key)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _manual_retry_count(headers: Mapping[str, Any]) -> int:
    return max(0, _header_int(headers, "x-relayna-manual-retry-count") or 0)


def _manual_retry_meta_from_status(meta: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(meta, Mapping):
        return {"count": 0, "previous_task_type": None, "source_consumer": None, "reason": None}
    manual_retry = meta.get("manual_retry")
    if not isinstance(manual_retry, Mapping):
        return {"count": 0, "previous_task_type": None, "source_consumer": None, "reason": None}
    return {
        "count": _coerce_non_negative_int(manual_retry.get("count"), fallback=0),
        "previous_task_type": _string_or_none(manual_retry.get("previous_task_type")),
        "source_consumer": _string_or_none(manual_retry.get("source_consumer")),
        "reason": _string_or_none(manual_retry.get("reason")),
    }


def _coerce_non_negative_int(value: Any, *, fallback: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return max(0, int(fallback))


def _merge_batch_retry_headers(context: TaskContext, headers: Mapping[str, Any] | None) -> dict[str, Any]:
    merged = dict(headers or {})
    if context.batch_id is not None:
        merged["batch_id"] = context.batch_id
    if context.batch_index is not None:
        merged["batch_index"] = context.batch_index
    if context.batch_size is not None:
        merged["batch_size"] = context.batch_size
    return merged


def _manual_retry_headers(
    headers: Mapping[str, Any],
    *,
    manual_retry_count: int,
    previous_task_type: str | None,
    source_consumer: str,
    reason: str | None,
    task_id: str,
    extra_headers: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    forwarded = {
        key: value
        for key, value in dict(headers).items()
        if key
        not in {
            "x-relayna-retry-attempt",
            "x-relayna-max-retries",
            "x-relayna-source-queue",
            "x-relayna-failure-reason",
            "x-relayna-exception-type",
            "x-relayna-manual-retry-count",
            "x-relayna-manual-retry-from-task-type",
            "x-relayna-manual-retry-source-consumer",
            "x-relayna-manual-retry-reason",
        }
    }
    forwarded["task_id"] = task_id
    forwarded["x-relayna-manual-retry-count"] = int(manual_retry_count)
    forwarded["x-relayna-manual-retry-source-consumer"] = source_consumer
    if previous_task_type:
        forwarded["x-relayna-manual-retry-from-task-type"] = previous_task_type
    if reason:
        forwarded["x-relayna-manual-retry-reason"] = reason
    forwarded.update(dict(extra_headers or {}))
    return forwarded


def _manual_retry_context_meta(context: TaskContext) -> dict[str, Any]:
    manual_retry: dict[str, Any] = {"count": int(context.manual_retry_count)}
    if context.manual_retry_previous_task_type:
        manual_retry["previous_task_type"] = context.manual_retry_previous_task_type
    if context.manual_retry_source_consumer:
        manual_retry["source_consumer"] = context.manual_retry_source_consumer
    if context.manual_retry_reason:
        manual_retry["reason"] = context.manual_retry_reason
    if len(manual_retry) == 1 and manual_retry["count"] == 0:
        return {}
    return {"manual_retry": manual_retry}


def _manual_retry_status_meta(
    *,
    manual_retry_count: int,
    previous_task_type: str | None,
    source_consumer: str,
    reason: str | None,
    target_task_type: str | None,
    extra_meta: Mapping[str, Any] | None,
) -> dict[str, Any]:
    merged = dict(extra_meta or {})
    manual_retry: dict[str, Any] = {"count": int(manual_retry_count), "source_consumer": source_consumer}
    if previous_task_type:
        manual_retry["previous_task_type"] = previous_task_type
    if reason:
        manual_retry["reason"] = reason
    if target_task_type:
        manual_retry["target_task_type"] = target_task_type
    existing = merged.get("manual_retry")
    if isinstance(existing, Mapping):
        merged["manual_retry"] = {**manual_retry, **dict(existing)}
    else:
        merged["manual_retry"] = manual_retry
    return merged


def _failure_message(exc: Exception, *, include_error_message: bool) -> str:
    if include_error_message:
        text = str(exc).strip()
        if text:
            return text
    return "Task processing failed."


def _normalize_payload(payload: Any, *, alias_config: ContractAliasConfig | None) -> Any:
    if isinstance(payload, Mapping):
        return normalize_contract_aliases(payload, alias_config, drop_aliases=True)
    return payload


def _normalize_batch_payload(payload: Mapping[str, Any], *, alias_config: ContractAliasConfig | None) -> dict[str, Any]:
    normalized = normalize_contract_aliases(payload, alias_config, drop_aliases=True)
    tasks = normalized.get("tasks")
    if isinstance(tasks, list):
        normalized["tasks"] = [_normalize_payload(task, alias_config=alias_config) for task in tasks]
    return normalized


def _to_json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


async def _persist_dlq_record(
    dlq_store: DLQRecorder | None,
    *,
    consumer_name: str,
    queue_name: str,
    source_queue_name: str,
    retry_queue_name: str,
    task_id: str | None,
    correlation_id: str | None,
    reason: str,
    exception_type: str | None,
    retry_attempt: int,
    max_retries: int,
    headers: Mapping[str, Any],
    content_type: str | None,
    body: bytes,
    observation_sink: ObservationSink | None = None,
) -> None:
    if dlq_store is None:
        return
    try:
        await dlq_store.add(
            build_dlq_record(
                queue_name=queue_name,
                source_queue_name=source_queue_name,
                retry_queue_name=retry_queue_name,
                task_id=task_id,
                correlation_id=correlation_id,
                reason=reason,
                exception_type=exception_type,
                retry_attempt=retry_attempt,
                max_retries=max_retries,
                headers=dict(headers),
                content_type=content_type,
                body=body,
            )
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        await emit_observation(
            observation_sink,
            ConsumerDLQRecordPersistFailed(
                consumer_name=consumer_name,
                task_id=task_id,
                queue_name=queue_name,
                retry_attempt=retry_attempt,
                max_retries=max_retries,
                reason=reason,
                exception_type=type(exc).__name__,
            ),
        )
        return


__all__ = [
    "AggregationHandler",
    "FailureAction",
    "LifecycleStatusConfig",
    "RetryPolicy",
    "RetryStatusConfig",
    "TaskContext",
    "TaskHandler",
    "WorkflowContext",
    "WorkflowHandler",
    "_ManualRetryRequest",
    "_ManualRetryRequested",
    "_coerce_task_id",
    "_failure_message",
    "_header_int",
    "_header_string",
    "_manual_retry_count",
    "_manual_retry_meta_from_status",
    "_merge_batch_retry_headers",
    "_message_headers",
    "_normalize_batch_payload",
    "_normalize_payload",
    "_persist_dlq_record",
    "_retry_attempt",
    "_retry_headers",
    "_resolve_workflow_stage_for_routing_key",
    "_string_or_none",
    "_to_json_bytes",
]
