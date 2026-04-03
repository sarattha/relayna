from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from functools import cache
from typing import Any, Protocol

from pydantic import ValidationError

from .contracts import (
    BatchTaskEnvelope,
    ContractAliasConfig,
    StatusEventEnvelope,
    TaskEnvelope,
    WorkflowEnvelope,
    is_batch_task_payload,
    normalize_contract_aliases,
)
from .dlq import DLQRecorder, build_dlq_record
from .observability import (
    ConsumerDeadLetterPublished,
    ConsumerDLQRecordPersistFailed,
    ConsumerRetryScheduled,
    ObservationSink,
    TaskConsumerLoopError,
    TaskConsumerStarted,
    TaskHandlerFailed,
    TaskLifecycleStatusPublished,
    TaskMessageAcked,
    TaskMessageReceived,
    TaskMessageRejected,
    WorkflowMessagePublished,
    WorkflowMessageReceived,
    WorkflowStageAcked,
    WorkflowStageFailed,
    WorkflowStageStarted,
    emit_observation,
)
from .rabbitmq import RelaynaRabbitClient, RetryInfrastructure
from .topology import RelaynaTopology, RoutedTasksSharedStatusTopology, SharedStatusWorkflowTopology


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


class TaskConsumer:
    def __init__(
        self,
        *,
        rabbitmq: RelaynaRabbitClient,
        handler: TaskHandler,
        consumer_name: str = "relayna-task-consumer",
        prefetch: int | None = None,
        consume_arguments: dict[str, Any] | None = None,
        failure_action: FailureAction = FailureAction.REJECT,
        lifecycle_statuses: LifecycleStatusConfig | None = None,
        retry_policy: RetryPolicy | None = None,
        retry_statuses: RetryStatusConfig | None = None,
        idle_retry_seconds: float = 2.0,
        consume_timeout_seconds: float | None = 1.0,
        observation_sink: ObservationSink | None = None,
        dlq_store: DLQRecorder | None = None,
        alias_config: ContractAliasConfig | None = None,
    ) -> None:
        self._rabbitmq = rabbitmq
        self._handler = handler
        self._consumer_name = consumer_name
        self._prefetch = prefetch
        self._consume_arguments = consume_arguments or {}
        self._failure_action = failure_action
        self._lifecycle_statuses = lifecycle_statuses or LifecycleStatusConfig()
        self._retry_policy = retry_policy
        self._retry_statuses = retry_statuses or RetryStatusConfig()
        self._idle_retry_seconds = idle_retry_seconds
        self._consume_timeout_seconds = consume_timeout_seconds
        self._observation_sink = observation_sink
        self._dlq_store = dlq_store
        self._alias_config = alias_config or getattr(rabbitmq, "alias_config", None)
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run_forever(self) -> None:
        queue_name: str | None = None
        retry_infrastructure: RetryInfrastructure | None = None
        prefetch = self._prefetch or self._rabbitmq.topology.prefetch_count
        started = False

        while not self._stop.is_set():
            channel = None
            try:
                if queue_name is None:
                    queue_name = await self._rabbitmq.ensure_tasks_queue()
                if retry_infrastructure is None and self._retry_policy is not None:
                    retry_infrastructure = await self._rabbitmq.ensure_retry_infrastructure(
                        source_queue_name=queue_name,
                        delay_ms=self._retry_policy.delay_ms,
                        retry_queue_suffix=self._retry_policy.retry_queue_suffix,
                        dead_letter_queue_suffix=self._retry_policy.dead_letter_queue_suffix,
                    )
                if not started:
                    await emit_observation(
                        self._observation_sink,
                        TaskConsumerStarted(consumer_name=self._consumer_name, queue_name=queue_name),
                    )
                    started = True

                channel = await self._rabbitmq.acquire_channel(prefetch=prefetch)
                queue = await channel.declare_queue(
                    queue_name,
                    durable=True,
                    arguments=self._rabbitmq.topology.task_queue_arguments() or None,
                )
                async with queue.iterator(
                    arguments=self._consume_arguments or None,
                    timeout=self._consume_timeout_seconds,
                ) as iterator:
                    async for message in iterator:
                        await self._handle_message(
                            message,
                            source_queue_name=queue_name,
                            retry_infrastructure=retry_infrastructure,
                        )
                        if self._stop.is_set():
                            break
            except asyncio.CancelledError:
                raise
            except TimeoutError:
                if self._stop.is_set():
                    return
                await asyncio.sleep(0)
                continue
            except Exception as exc:
                if self._stop.is_set():
                    return
                await emit_observation(
                    self._observation_sink,
                    TaskConsumerLoopError(
                        consumer_name=self._consumer_name,
                        exception_type=type(exc).__name__,
                        retry_delay_seconds=self._idle_retry_seconds,
                    ),
                )
                await asyncio.sleep(self._idle_retry_seconds)
            finally:
                if channel is not None:
                    try:
                        await channel.close()
                    except Exception:
                        pass

    async def _handle_message(
        self,
        message: Any,
        *,
        source_queue_name: str,
        retry_infrastructure: RetryInfrastructure | None,
    ) -> None:
        try:
            payload = json.loads(message.body.decode("utf-8", errors="replace"))
        except Exception:
            if self._retry_policy is None:
                await message.reject(requeue=False)
                await emit_observation(
                    self._observation_sink,
                    TaskMessageRejected(
                        consumer_name=self._consumer_name,
                        task_id=None,
                        requeue=False,
                        reason="malformed_json",
                    ),
                )
            else:
                await self._publish_dead_letter(
                    message,
                    retry_infrastructure=retry_infrastructure,
                    source_queue_name=source_queue_name,
                    task_id=None,
                    retry_attempt=_retry_attempt(message),
                    reason="malformed_json",
                    exception_type=None,
                )
                await message.ack()
            return

        normalized_payload = _normalize_payload(payload, alias_config=self._alias_config)
        await emit_observation(
            self._observation_sink,
            TaskMessageReceived(
                consumer_name=self._consumer_name,
                task_id=_coerce_task_id(normalized_payload),
                delivery_tag=getattr(message, "delivery_tag", None),
                redelivered=bool(getattr(message, "redelivered", False)),
            ),
        )

        if isinstance(normalized_payload, Mapping) and is_batch_task_payload(normalized_payload):
            if self._retry_policy is None:
                await message.reject(requeue=False)
                await emit_observation(
                    self._observation_sink,
                    TaskMessageRejected(
                        consumer_name=self._consumer_name,
                        task_id=None,
                        requeue=False,
                        reason="unsupported_batch_envelope",
                    ),
                )
                return
            await self._handle_batch_message(
                message,
                payload=normalized_payload,
                source_queue_name=source_queue_name,
                retry_infrastructure=retry_infrastructure,
            )
            return

        try:
            task = TaskEnvelope.model_validate(normalized_payload)
        except ValidationError:
            if self._retry_policy is None:
                await message.reject(requeue=False)
                await emit_observation(
                    self._observation_sink,
                    TaskMessageRejected(
                        consumer_name=self._consumer_name,
                        task_id=_coerce_task_id(normalized_payload),
                        requeue=False,
                        reason="invalid_envelope",
                    ),
                )
            else:
                await self._publish_dead_letter(
                    message,
                    retry_infrastructure=retry_infrastructure,
                    source_queue_name=source_queue_name,
                    task_id=_coerce_task_id(normalized_payload),
                    retry_attempt=_retry_attempt(message),
                    reason="invalid_envelope",
                    exception_type="ValidationError",
                )
                await message.ack()
            return

        context = self._make_task_context(
            task=task,
            raw_payload=dict(normalized_payload),
            message=message,
            source_queue_name=source_queue_name,
        )
        success = await self._process_task(
            task=task,
            context=context,
            message=message,
            retry_infrastructure=retry_infrastructure,
            source_queue_name=source_queue_name,
        )
        if success:
            await message.ack()
            await emit_observation(
                self._observation_sink,
                TaskMessageAcked(consumer_name=self._consumer_name, task_id=task.task_id),
            )

    async def _handle_batch_message(
        self,
        message: Any,
        *,
        payload: Mapping[str, Any],
        source_queue_name: str,
        retry_infrastructure: RetryInfrastructure | None,
    ) -> None:
        try:
            batch = BatchTaskEnvelope.model_validate(_normalize_batch_payload(payload, alias_config=self._alias_config))
        except ValidationError:
            await self._publish_dead_letter(
                message,
                retry_infrastructure=retry_infrastructure,
                source_queue_name=source_queue_name,
                task_id=None,
                retry_attempt=_retry_attempt(message),
                reason="invalid_envelope",
                exception_type="ValidationError",
            )
            await message.ack()
            return

        batch_size = len(batch.tasks)
        if isinstance(self._rabbitmq.topology, RoutedTasksSharedStatusTopology):
            task_types = {str(task.task_type).strip() for task in batch.tasks if str(task.task_type).strip()}
            if len(task_types) > 1:
                await self._publish_dead_letter(
                    message,
                    retry_infrastructure=retry_infrastructure,
                    source_queue_name=source_queue_name,
                    task_id=None,
                    retry_attempt=_retry_attempt(message),
                    reason="mixed_task_type_batch",
                    exception_type="ValueError",
                )
                await message.ack()
                return
        for index, task in enumerate(batch.tasks):
            task_payload = task.model_dump(mode="json", exclude_none=True)
            await self._rabbitmq.publish_raw_to_queue(
                source_queue_name,
                _to_json_bytes(task_payload),
                correlation_id=task.correlation_id or task.task_id,
                headers={
                    "task_id": task.task_id,
                    "batch_id": batch.batch_id,
                    "batch_index": index,
                    "batch_size": batch_size,
                },
                content_type=getattr(message, "content_type", "application/json"),
            )

        await message.ack()
        await emit_observation(
            self._observation_sink,
            TaskMessageAcked(consumer_name=self._consumer_name, task_id=None),
        )

    def _make_task_context(
        self,
        *,
        task: TaskEnvelope,
        raw_payload: dict[str, Any],
        message: Any,
        source_queue_name: str,
        batch_id: str | None = None,
        batch_index: int | None = None,
        batch_size: int | None = None,
    ) -> TaskContext:
        headers = _message_headers(message)
        return TaskContext(
            rabbitmq=self._rabbitmq,
            consumer_name=self._consumer_name,
            raw_payload=dict(raw_payload),
            correlation_id=task.correlation_id or getattr(message, "correlation_id", None),
            delivery_tag=getattr(message, "delivery_tag", None),
            redelivered=bool(getattr(message, "redelivered", False)),
            _task_id=task.task_id,
            retry_attempt=_retry_attempt(message),
            max_retries=self._retry_policy.max_retries if self._retry_policy is not None else None,
            source_queue_name=source_queue_name,
            batch_id=batch_id if batch_id is not None else _header_string(headers, "batch_id"),
            batch_index=batch_index if batch_index is not None else _header_int(headers, "batch_index"),
            batch_size=batch_size if batch_size is not None else _header_int(headers, "batch_size"),
            headers=headers,
            manual_retry_count=_manual_retry_count(headers),
            manual_retry_previous_task_type=_header_string(headers, "x-relayna-manual-retry-from-task-type"),
            manual_retry_source_consumer=_header_string(headers, "x-relayna-manual-retry-source-consumer"),
            manual_retry_reason=_header_string(headers, "x-relayna-manual-retry-reason"),
        )

    async def _process_task(
        self,
        *,
        task: TaskEnvelope,
        context: TaskContext,
        message: Any,
        retry_infrastructure: RetryInfrastructure | None,
        source_queue_name: str,
        ack_message: bool = True,
        retry_body: bytes | None = None,
        retry_correlation_id: str | None = None,
        retry_headers: Mapping[str, Any] | None = None,
    ) -> bool:
        try:
            await self._publish_lifecycle_status(
                context,
                status=self._lifecycle_statuses.processing_status,
                message="Task processing started.",
            )
            try:
                await self._handler(task, context)
            except _ManualRetryRequested:
                pass
            if context._manual_retry_request is not None:
                await self._publish_manual_retry_request(context, context._manual_retry_request)
                return True
            await self._publish_lifecycle_status(
                context,
                status=self._lifecycle_statuses.completed_status,
                message="Task processing completed.",
            )
            return True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await emit_observation(
                self._observation_sink,
                TaskHandlerFailed(
                    consumer_name=self._consumer_name,
                    task_id=task.task_id,
                    exception_type=type(exc).__name__,
                    requeue=self._retry_policy is None and self._failure_action is FailureAction.REQUEUE,
                ),
            )
            if self._retry_policy is None:
                failed_message = _failure_message(
                    exc, include_error_message=self._lifecycle_statuses.include_error_message
                )
                await self._publish_lifecycle_status(
                    context,
                    status=self._lifecycle_statuses.failed_status,
                    message=failed_message,
                )
                if ack_message:
                    requeue = self._failure_action is FailureAction.REQUEUE
                    await message.reject(requeue=requeue)
                    await emit_observation(
                        self._observation_sink,
                        TaskMessageRejected(
                            consumer_name=self._consumer_name,
                            task_id=task.task_id,
                            requeue=requeue,
                            reason="handler_error",
                        ),
                    )
                return False

            max_retries = self._retry_policy.max_retries
            if context.retry_attempt < max_retries:
                next_attempt = context.retry_attempt + 1
                await self._publish_retry(
                    message,
                    retry_infrastructure=retry_infrastructure,
                    source_queue_name=source_queue_name,
                    task_id=task.task_id,
                    retry_attempt=next_attempt,
                    max_retries=max_retries,
                    reason="handler_error",
                    exception_type=type(exc).__name__,
                    body=retry_body,
                    correlation_id=retry_correlation_id,
                    extra_headers=_merge_batch_retry_headers(context, retry_headers),
                )
                await self._publish_retry_status(context, next_attempt=next_attempt, max_retries=max_retries)
                if ack_message:
                    await message.ack()
                return False

            await self._publish_dead_letter(
                message,
                retry_infrastructure=retry_infrastructure,
                source_queue_name=source_queue_name,
                task_id=task.task_id,
                retry_attempt=context.retry_attempt,
                reason="handler_error",
                exception_type=type(exc).__name__,
                body=retry_body,
                correlation_id=retry_correlation_id,
                extra_headers=_merge_batch_retry_headers(context, retry_headers),
            )
            await self._publish_dead_letter_status(context, exc)
            if ack_message:
                await message.ack()
            return False

    async def _publish_manual_retry_request(self, context: TaskContext, request: _ManualRetryRequest) -> None:
        try:
            await context.publish_status(
                status="manual_retrying",
                message=request.status_message,
                meta=request.status_meta,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        await self._rabbitmq.publish_task(request.task, headers=request.headers)

    async def _publish_lifecycle_status(self, context: TaskContext, *, status: str, message: str) -> None:
        if not self._lifecycle_statuses.enabled:
            return
        try:
            await context.publish_status(status=status, message=message)
            await emit_observation(
                self._observation_sink,
                TaskLifecycleStatusPublished(
                    consumer_name=self._consumer_name,
                    task_id=context._task_id,
                    status=status,
                ),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    async def _publish_retry_status(self, context: TaskContext, *, next_attempt: int, max_retries: int) -> None:
        if not self._retry_statuses.enabled:
            return
        try:
            await context.publish_status(
                status=self._retry_statuses.retrying_status,
                message=f"Task processing failed. Scheduling retry {next_attempt}/{max_retries}.",
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    async def _publish_dead_letter_status(self, context: TaskContext, exc: Exception) -> None:
        if self._retry_statuses.enabled:
            try:
                await context.publish_status(
                    status=self._retry_statuses.dead_lettered_status,
                    message=_failure_message(exc, include_error_message=self._retry_statuses.include_error_message),
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                return
            return
        if not self._lifecycle_statuses.enabled:
            return
        await self._publish_lifecycle_status(
            context,
            status=self._lifecycle_statuses.failed_status,
            message=_failure_message(exc, include_error_message=self._lifecycle_statuses.include_error_message),
        )

    async def _publish_retry(
        self,
        message: Any,
        *,
        retry_infrastructure: RetryInfrastructure | None,
        source_queue_name: str,
        task_id: str | None,
        retry_attempt: int,
        max_retries: int,
        reason: str,
        exception_type: str | None,
        body: bytes | None = None,
        correlation_id: str | None = None,
        extra_headers: Mapping[str, Any] | None = None,
    ) -> None:
        if retry_infrastructure is None:
            raise RuntimeError("Retry infrastructure is not initialized")
        headers = _retry_headers(
            message,
            source_queue_name=source_queue_name,
            retry_attempt=retry_attempt,
            max_retries=max_retries,
            reason=reason,
            exception_type=exception_type,
            extra_headers=extra_headers,
        )
        await self._rabbitmq.publish_raw_to_queue(
            retry_infrastructure.retry_queue_name,
            body or message.body,
            correlation_id=correlation_id or getattr(message, "correlation_id", None),
            headers=headers,
            content_type=getattr(message, "content_type", "application/json"),
        )
        await emit_observation(
            self._observation_sink,
            ConsumerRetryScheduled(
                consumer_name=self._consumer_name,
                task_id=task_id,
                queue_name=retry_infrastructure.retry_queue_name,
                retry_attempt=retry_attempt,
                max_retries=max_retries,
                reason=reason,
            ),
        )

    async def _publish_dead_letter(
        self,
        message: Any,
        *,
        retry_infrastructure: RetryInfrastructure | None,
        source_queue_name: str,
        task_id: str | None,
        retry_attempt: int,
        reason: str,
        exception_type: str | None,
        body: bytes | None = None,
        correlation_id: str | None = None,
        extra_headers: Mapping[str, Any] | None = None,
    ) -> None:
        if retry_infrastructure is None or self._retry_policy is None:
            raise RuntimeError("Retry infrastructure is not initialized")
        headers = _retry_headers(
            message,
            source_queue_name=source_queue_name,
            retry_attempt=retry_attempt,
            max_retries=self._retry_policy.max_retries,
            reason=reason,
            exception_type=exception_type,
            extra_headers=extra_headers,
        )
        await self._rabbitmq.publish_raw_to_queue(
            retry_infrastructure.dead_letter_queue_name,
            body or message.body,
            correlation_id=correlation_id or getattr(message, "correlation_id", None),
            headers=headers,
            content_type=getattr(message, "content_type", "application/json"),
        )
        await emit_observation(
            self._observation_sink,
            ConsumerDeadLetterPublished(
                consumer_name=self._consumer_name,
                task_id=task_id,
                queue_name=retry_infrastructure.dead_letter_queue_name,
                retry_attempt=retry_attempt,
                max_retries=self._retry_policy.max_retries,
                reason=reason,
            ),
        )
        await _persist_dlq_record(
            self._dlq_store,
            consumer_name=self._consumer_name,
            queue_name=retry_infrastructure.dead_letter_queue_name,
            source_queue_name=source_queue_name,
            retry_queue_name=retry_infrastructure.retry_queue_name,
            task_id=task_id,
            correlation_id=correlation_id or getattr(message, "correlation_id", None),
            reason=reason,
            exception_type=exception_type,
            retry_attempt=retry_attempt,
            max_retries=self._retry_policy.max_retries,
            headers=headers,
            content_type=getattr(message, "content_type", "application/json"),
            body=body or message.body,
            observation_sink=self._observation_sink,
        )


class WorkflowConsumer:
    def __init__(
        self,
        *,
        rabbitmq: RelaynaRabbitClient,
        handler: WorkflowHandler,
        stage: str | None = None,
        queue_name: str | None = None,
        consumer_name: str = "relayna-workflow-consumer",
        prefetch: int | None = None,
        consume_arguments: dict[str, Any] | None = None,
        retry_policy: RetryPolicy | None = None,
        retry_statuses: RetryStatusConfig | None = None,
        idle_retry_seconds: float = 2.0,
        consume_timeout_seconds: float | None = 1.0,
        observation_sink: ObservationSink | None = None,
        dlq_store: DLQRecorder | None = None,
        alias_config: ContractAliasConfig | None = None,
    ) -> None:
        if stage is None and queue_name is None:
            raise ValueError("WorkflowConsumer requires stage=... or queue_name=...")
        self._rabbitmq = rabbitmq
        self._handler = handler
        self._stage = stage
        self._queue_name = queue_name
        self._consumer_name = consumer_name
        self._prefetch = prefetch
        self._consume_arguments = consume_arguments or {}
        self._retry_policy = retry_policy
        self._retry_statuses = retry_statuses or RetryStatusConfig()
        self._idle_retry_seconds = idle_retry_seconds
        self._consume_timeout_seconds = consume_timeout_seconds
        self._observation_sink = observation_sink
        self._dlq_store = dlq_store
        self._alias_config = alias_config or getattr(rabbitmq, "alias_config", None)
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run_forever(self) -> None:
        queue_name: str | None = self._queue_name
        retry_infrastructure: RetryInfrastructure | None = None
        prefetch = self._prefetch or self._rabbitmq.topology.prefetch_count
        started = False
        stage = self._resolve_stage()

        while not self._stop.is_set():
            channel = None
            try:
                if queue_name is None:
                    queue_name = await self._rabbitmq.ensure_workflow_queue(stage)
                if retry_infrastructure is None and self._retry_policy is not None:
                    retry_infrastructure = await self._rabbitmq.ensure_retry_infrastructure(
                        source_queue_name=queue_name,
                        delay_ms=self._retry_policy.delay_ms,
                        retry_queue_suffix=self._retry_policy.retry_queue_suffix,
                        dead_letter_queue_suffix=self._retry_policy.dead_letter_queue_suffix,
                    )
                if not started:
                    await emit_observation(
                        self._observation_sink,
                        WorkflowStageStarted(
                            consumer_name=self._consumer_name,
                            stage=stage,
                            queue_name=queue_name,
                        ),
                    )
                    started = True

                channel = await self._rabbitmq.acquire_channel(prefetch=prefetch)
                queue = await channel.declare_queue(
                    queue_name,
                    durable=True,
                    arguments=self._rabbitmq.topology.workflow_queue_arguments(stage) or None,
                )
                async with queue.iterator(
                    arguments=self._consume_arguments or None,
                    timeout=self._consume_timeout_seconds,
                ) as iterator:
                    async for message in iterator:
                        acked = await self._handle_message(
                            message,
                            stage=stage,
                            source_queue_name=queue_name,
                            retry_infrastructure=retry_infrastructure,
                        )
                        if acked:
                            await message.ack()
                        if self._stop.is_set():
                            break
            except asyncio.CancelledError:
                raise
            except TimeoutError:
                if self._stop.is_set():
                    return
                await asyncio.sleep(0)
                continue
            except Exception as exc:
                if self._stop.is_set():
                    return
                await emit_observation(
                    self._observation_sink,
                    WorkflowStageFailed(
                        consumer_name=self._consumer_name,
                        queue_name=queue_name,
                        stage=stage,
                        routing_key=None,
                        task_id=None,
                        message_id=None,
                        origin_stage=None,
                        correlation_id=None,
                        exception_type=type(exc).__name__,
                        requeue=False,
                    ),
                )
                await asyncio.sleep(self._idle_retry_seconds)
            finally:
                if channel is not None:
                    try:
                        await channel.close()
                    except Exception:
                        pass

    async def _handle_message(
        self,
        message: Any,
        *,
        stage: str,
        source_queue_name: str,
        retry_infrastructure: RetryInfrastructure | None,
    ) -> bool:
        try:
            payload = json.loads(message.body.decode("utf-8", errors="replace"))
        except Exception:
            if self._retry_policy is None:
                await message.reject(requeue=False)
            else:
                await self._publish_dead_letter(
                    message,
                    retry_infrastructure=retry_infrastructure,
                    source_queue_name=source_queue_name,
                    task_id=None,
                    retry_attempt=_retry_attempt(message),
                    reason="malformed_json",
                    exception_type=None,
                )
                return True
            return False

        normalized_payload = _normalize_payload(payload, alias_config=self._alias_config)
        try:
            workflow_message = WorkflowEnvelope.model_validate(normalized_payload)
        except ValidationError:
            if self._retry_policy is None:
                await message.reject(requeue=False)
            else:
                await self._publish_dead_letter(
                    message,
                    retry_infrastructure=retry_infrastructure,
                    source_queue_name=source_queue_name,
                    task_id=_coerce_task_id(normalized_payload),
                    retry_attempt=_retry_attempt(message),
                    reason="invalid_envelope",
                    exception_type="ValidationError",
                )
                return True
            return False

        await emit_observation(
            self._observation_sink,
            WorkflowMessageReceived(
                consumer_name=self._consumer_name,
                queue_name=source_queue_name,
                stage=stage,
                routing_key=_header_string(_message_headers(message), "routing_key"),
                task_id=workflow_message.task_id,
                message_id=workflow_message.message_id,
                origin_stage=workflow_message.origin_stage,
                correlation_id=workflow_message.correlation_id or getattr(message, "correlation_id", None),
                delivery_tag=getattr(message, "delivery_tag", None),
                redelivered=bool(getattr(message, "redelivered", False)),
            ),
        )

        context = WorkflowContext(
            rabbitmq=self._rabbitmq,
            consumer_name=self._consumer_name,
            stage=stage,
            raw_payload=workflow_message.model_dump(mode="json", exclude_none=True),
            correlation_id=workflow_message.correlation_id or getattr(message, "correlation_id", None),
            delivery_tag=getattr(message, "delivery_tag", None),
            redelivered=bool(getattr(message, "redelivered", False)),
            _task_id=workflow_message.task_id,
            _message_id=workflow_message.message_id,
            origin_stage=workflow_message.origin_stage,
            retry_attempt=_retry_attempt(message),
            max_retries=self._retry_policy.max_retries if self._retry_policy is not None else None,
            source_queue_name=source_queue_name,
            headers=_message_headers(message),
            observation_sink=self._observation_sink,
        )

        try:
            await self._handler(workflow_message, context)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await emit_observation(
                self._observation_sink,
                WorkflowStageFailed(
                    consumer_name=self._consumer_name,
                    queue_name=source_queue_name,
                    stage=stage,
                    routing_key=None,
                    task_id=workflow_message.task_id,
                    message_id=workflow_message.message_id,
                    origin_stage=workflow_message.origin_stage,
                    correlation_id=context.correlation_id,
                    exception_type=type(exc).__name__,
                    requeue=False,
                ),
            )
            if self._retry_policy is None:
                await message.reject(requeue=False)
                return False

            max_retries = self._retry_policy.max_retries
            if context.retry_attempt < max_retries:
                next_attempt = context.retry_attempt + 1
                await self._publish_retry(
                    message,
                    retry_infrastructure=retry_infrastructure,
                    source_queue_name=source_queue_name,
                    task_id=workflow_message.task_id,
                    retry_attempt=next_attempt,
                    max_retries=max_retries,
                    reason="handler_error",
                    exception_type=type(exc).__name__,
                )
                await self._publish_retry_status(context, next_attempt=next_attempt, max_retries=max_retries)
                return True

            await self._publish_dead_letter(
                message,
                retry_infrastructure=retry_infrastructure,
                source_queue_name=source_queue_name,
                task_id=workflow_message.task_id,
                retry_attempt=context.retry_attempt,
                reason="handler_error",
                exception_type=type(exc).__name__,
            )
            await self._publish_dead_letter_status(context, exc)
            return True

        await emit_observation(
            self._observation_sink,
            WorkflowStageAcked(
                consumer_name=self._consumer_name,
                queue_name=source_queue_name,
                stage=stage,
                routing_key=None,
                task_id=workflow_message.task_id,
                message_id=workflow_message.message_id,
                origin_stage=workflow_message.origin_stage,
                correlation_id=context.correlation_id,
            ),
        )
        return True

    async def _publish_retry_status(self, context: WorkflowContext, *, next_attempt: int, max_retries: int) -> None:
        if not self._retry_statuses.enabled:
            return
        try:
            await context.publish_status(
                status=self._retry_statuses.retrying_status,
                message=f"Task processing failed. Scheduling retry {next_attempt}/{max_retries}.",
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    async def _publish_dead_letter_status(self, context: WorkflowContext, exc: Exception) -> None:
        if not self._retry_statuses.enabled:
            return
        try:
            await context.publish_status(
                status=self._retry_statuses.dead_lettered_status,
                message=_failure_message(exc, include_error_message=self._retry_statuses.include_error_message),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    async def _publish_retry(
        self,
        message: Any,
        *,
        retry_infrastructure: RetryInfrastructure | None,
        source_queue_name: str,
        task_id: str | None,
        retry_attempt: int,
        max_retries: int,
        reason: str,
        exception_type: str | None,
    ) -> None:
        if retry_infrastructure is None:
            raise RuntimeError("Retry infrastructure is not initialized")
        headers = _retry_headers(
            message,
            source_queue_name=source_queue_name,
            retry_attempt=retry_attempt,
            max_retries=max_retries,
            reason=reason,
            exception_type=exception_type,
        )
        await self._rabbitmq.publish_raw_to_queue(
            retry_infrastructure.retry_queue_name,
            message.body,
            correlation_id=getattr(message, "correlation_id", None),
            headers=headers,
            content_type=getattr(message, "content_type", "application/json"),
        )
        await emit_observation(
            self._observation_sink,
            ConsumerRetryScheduled(
                consumer_name=self._consumer_name,
                task_id=task_id,
                queue_name=retry_infrastructure.retry_queue_name,
                retry_attempt=retry_attempt,
                max_retries=max_retries,
                reason=reason,
            ),
        )

    async def _publish_dead_letter(
        self,
        message: Any,
        *,
        retry_infrastructure: RetryInfrastructure | None,
        source_queue_name: str,
        task_id: str | None,
        retry_attempt: int,
        reason: str,
        exception_type: str | None,
    ) -> None:
        if retry_infrastructure is None or self._retry_policy is None:
            raise RuntimeError("Retry infrastructure is not initialized")
        headers = _retry_headers(
            message,
            source_queue_name=source_queue_name,
            retry_attempt=retry_attempt,
            max_retries=self._retry_policy.max_retries,
            reason=reason,
            exception_type=exception_type,
        )
        await self._rabbitmq.publish_raw_to_queue(
            retry_infrastructure.dead_letter_queue_name,
            message.body,
            correlation_id=getattr(message, "correlation_id", None),
            headers=headers,
            content_type=getattr(message, "content_type", "application/json"),
        )
        await emit_observation(
            self._observation_sink,
            ConsumerDeadLetterPublished(
                consumer_name=self._consumer_name,
                task_id=task_id,
                queue_name=retry_infrastructure.dead_letter_queue_name,
                retry_attempt=retry_attempt,
                max_retries=self._retry_policy.max_retries,
                reason=reason,
            ),
        )
        await _persist_dlq_record(
            self._dlq_store,
            consumer_name=self._consumer_name,
            queue_name=retry_infrastructure.dead_letter_queue_name,
            source_queue_name=source_queue_name,
            retry_queue_name=retry_infrastructure.retry_queue_name,
            task_id=task_id,
            correlation_id=getattr(message, "correlation_id", None),
            reason=reason,
            exception_type=exception_type,
            retry_attempt=retry_attempt,
            max_retries=self._retry_policy.max_retries,
            headers=headers,
            content_type=getattr(message, "content_type", "application/json"),
            body=message.body,
            observation_sink=self._observation_sink,
        )

    def _resolve_stage(self) -> str:
        if self._stage is not None:
            return self._stage
        topology = self._rabbitmq.topology
        if not isinstance(topology, SharedStatusWorkflowTopology):
            raise RuntimeError("WorkflowConsumer requires SharedStatusWorkflowTopology when stage is omitted")
        if self._queue_name is None:
            raise RuntimeError("WorkflowConsumer requires stage=... or queue_name=...")
        for stage in topology.workflow_stage_names():
            if topology.workflow_queue_name(stage) == self._queue_name:
                return stage
        raise KeyError(f"No workflow stage is bound to queue '{self._queue_name}'")


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
            _topic_binding_matches(binding_key, routing_key)
            for binding_key in topology.workflow_binding_keys(stage)
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
                headers=headers,
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


class AggregationConsumer:
    """Consumes status-compatible aggregation messages from shard queues."""

    def __init__(
        self,
        *,
        rabbitmq: RelaynaRabbitClient,
        handler: AggregationHandler,
        shards: list[int],
        queue_name: str | None = None,
        consumer_name: str = "relayna-aggregation-consumer",
        prefetch: int | None = None,
        consume_arguments: dict[str, Any] | None = None,
        retry_policy: RetryPolicy | None = None,
        retry_statuses: RetryStatusConfig | None = None,
        idle_retry_seconds: float = 2.0,
        consume_timeout_seconds: float | None = 1.0,
        observation_sink: ObservationSink | None = None,
        dlq_store: DLQRecorder | None = None,
        alias_config: ContractAliasConfig | None = None,
    ) -> None:
        self._rabbitmq = rabbitmq
        self._handler = handler
        self._shards = list(shards)
        self._queue_name = queue_name
        self._consumer_name = consumer_name
        self._prefetch = prefetch
        self._consume_arguments = consume_arguments or {}
        self._retry_policy = retry_policy
        self._retry_statuses = retry_statuses or RetryStatusConfig()
        self._idle_retry_seconds = idle_retry_seconds
        self._consume_timeout_seconds = consume_timeout_seconds
        self._observation_sink = observation_sink
        self._dlq_store = dlq_store
        self._alias_config = alias_config or getattr(rabbitmq, "alias_config", None)
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run_forever(self) -> None:
        queue_name: str | None = self._queue_name
        retry_infrastructure: RetryInfrastructure | None = None
        prefetch = self._prefetch or self._rabbitmq.topology.prefetch_count

        while not self._stop.is_set():
            channel = None
            try:
                queue_name = await self._rabbitmq.ensure_aggregation_queue(
                    shards=self._shards,
                    queue_name=queue_name,
                )
                if retry_infrastructure is None and self._retry_policy is not None:
                    retry_infrastructure = await self._rabbitmq.ensure_retry_infrastructure(
                        source_queue_name=queue_name,
                        delay_ms=self._retry_policy.delay_ms,
                        retry_queue_suffix=self._retry_policy.retry_queue_suffix,
                        dead_letter_queue_suffix=self._retry_policy.dead_letter_queue_suffix,
                    )
                channel = await self._rabbitmq.acquire_channel(prefetch=prefetch)
                queue = await channel.declare_queue(
                    queue_name,
                    durable=True,
                    arguments=self._rabbitmq.topology.aggregation_queue_arguments() or None,
                )
                async with queue.iterator(
                    arguments=self._consume_arguments or None,
                    timeout=self._consume_timeout_seconds,
                ) as iterator:
                    async for message in iterator:
                        payload: Any = None
                        try:
                            payload = json.loads(message.body.decode("utf-8", errors="replace"))
                        except Exception:
                            if self._retry_policy is None:
                                await message.reject(requeue=False)
                            else:
                                await self._publish_dead_letter(
                                    message,
                                    retry_infrastructure=retry_infrastructure,
                                    source_queue_name=queue_name,
                                    task_id=None,
                                    retry_attempt=_retry_attempt(message),
                                    reason="malformed_json",
                                    exception_type=None,
                                )
                                await message.ack()
                            continue
                        payload = _normalize_payload(payload, alias_config=self._alias_config)
                        try:
                            event = StatusEventEnvelope.model_validate(payload)
                        except ValidationError:
                            if self._retry_policy is None:
                                await message.reject(requeue=False)
                            else:
                                await self._publish_dead_letter(
                                    message,
                                    retry_infrastructure=retry_infrastructure,
                                    source_queue_name=queue_name,
                                    task_id=_coerce_task_id(payload),
                                    retry_attempt=_retry_attempt(message),
                                    reason="invalid_envelope",
                                    exception_type="ValidationError",
                                )
                                await message.ack()
                            continue

                        current_retry_attempt = _retry_attempt(message)
                        headers = _message_headers(message)
                        manual_retry_meta = _manual_retry_meta_from_status(event.meta)
                        context = TaskContext(
                            rabbitmq=self._rabbitmq,
                            consumer_name=self._consumer_name,
                            raw_payload=dict(payload),
                            correlation_id=event.correlation_id or getattr(message, "correlation_id", None),
                            delivery_tag=getattr(message, "delivery_tag", None),
                            redelivered=bool(getattr(message, "redelivered", False)),
                            _task_id=event.task_id,
                            retry_attempt=current_retry_attempt,
                            max_retries=self._retry_policy.max_retries if self._retry_policy is not None else None,
                            source_queue_name=queue_name,
                            headers=headers,
                            is_task_context=False,
                            manual_retry_count=_manual_retry_count(headers) or manual_retry_meta["count"],
                            manual_retry_previous_task_type=_header_string(
                                headers, "x-relayna-manual-retry-from-task-type"
                            )
                            or manual_retry_meta["previous_task_type"],
                            manual_retry_source_consumer=_header_string(
                                headers, "x-relayna-manual-retry-source-consumer"
                            )
                            or manual_retry_meta["source_consumer"],
                            manual_retry_reason=_header_string(headers, "x-relayna-manual-retry-reason")
                            or manual_retry_meta["reason"],
                        )
                        try:
                            await self._handler(event, context)
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            if self._retry_policy is None:
                                await message.reject(requeue=False)
                                continue
                            max_retries = self._retry_policy.max_retries
                            if current_retry_attempt < max_retries:
                                next_attempt = current_retry_attempt + 1
                                await self._publish_retry(
                                    message,
                                    retry_infrastructure=retry_infrastructure,
                                    source_queue_name=queue_name,
                                    task_id=event.task_id,
                                    retry_attempt=next_attempt,
                                    max_retries=max_retries,
                                    reason="handler_error",
                                    exception_type=type(exc).__name__,
                                )
                                await self._publish_retry_status(
                                    context,
                                    next_attempt=next_attempt,
                                    max_retries=max_retries,
                                    meta=event.meta,
                                )
                                await message.ack()
                                continue
                            await self._publish_dead_letter(
                                message,
                                retry_infrastructure=retry_infrastructure,
                                source_queue_name=queue_name,
                                task_id=event.task_id,
                                retry_attempt=current_retry_attempt,
                                reason="handler_error",
                                exception_type=type(exc).__name__,
                            )
                            await self._publish_dead_letter_status(context, exc, meta=event.meta)
                            await message.ack()
                            continue
                        await message.ack()
                        if self._stop.is_set():
                            break
            except asyncio.CancelledError:
                raise
            except TimeoutError:
                if self._stop.is_set():
                    return
                await asyncio.sleep(0)
            except Exception:
                if self._stop.is_set():
                    return
                await asyncio.sleep(self._idle_retry_seconds)
            finally:
                if channel is not None:
                    try:
                        await channel.close()
                    except Exception:
                        pass

    async def _publish_retry_status(
        self,
        context: TaskContext,
        *,
        next_attempt: int,
        max_retries: int,
        meta: Mapping[str, Any],
    ) -> None:
        if not self._retry_statuses.enabled:
            return
        try:
            await context.publish_status(
                status=self._retry_statuses.retrying_status,
                message=f"Task processing failed. Scheduling retry {next_attempt}/{max_retries}.",
                meta=dict(meta),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    async def _publish_dead_letter_status(
        self,
        context: TaskContext,
        exc: Exception,
        *,
        meta: Mapping[str, Any],
    ) -> None:
        if not self._retry_statuses.enabled:
            return
        try:
            await context.publish_status(
                status=self._retry_statuses.dead_lettered_status,
                message=_failure_message(exc, include_error_message=self._retry_statuses.include_error_message),
                meta=dict(meta),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    async def _publish_retry(
        self,
        message: Any,
        *,
        retry_infrastructure: RetryInfrastructure | None,
        source_queue_name: str,
        task_id: str | None,
        retry_attempt: int,
        max_retries: int,
        reason: str,
        exception_type: str | None,
    ) -> None:
        if retry_infrastructure is None:
            raise RuntimeError("Retry infrastructure is not initialized")
        await self._rabbitmq.publish_raw_to_queue(
            retry_infrastructure.retry_queue_name,
            message.body,
            correlation_id=getattr(message, "correlation_id", None),
            headers=_retry_headers(
                message,
                source_queue_name=source_queue_name,
                retry_attempt=retry_attempt,
                max_retries=max_retries,
                reason=reason,
                exception_type=exception_type,
            ),
            content_type=getattr(message, "content_type", "application/json"),
        )
        await emit_observation(
            self._observation_sink,
            ConsumerRetryScheduled(
                consumer_name=self._consumer_name,
                task_id=task_id,
                queue_name=retry_infrastructure.retry_queue_name,
                retry_attempt=retry_attempt,
                max_retries=max_retries,
                reason=reason,
            ),
        )

    async def _publish_dead_letter(
        self,
        message: Any,
        *,
        retry_infrastructure: RetryInfrastructure | None,
        source_queue_name: str,
        task_id: str | None,
        retry_attempt: int,
        reason: str,
        exception_type: str | None,
    ) -> None:
        if retry_infrastructure is None or self._retry_policy is None:
            raise RuntimeError("Retry infrastructure is not initialized")
        await self._rabbitmq.publish_raw_to_queue(
            retry_infrastructure.dead_letter_queue_name,
            message.body,
            correlation_id=getattr(message, "correlation_id", None),
            headers=_retry_headers(
                message,
                source_queue_name=source_queue_name,
                retry_attempt=retry_attempt,
                max_retries=self._retry_policy.max_retries,
                reason=reason,
                exception_type=exception_type,
            ),
            content_type=getattr(message, "content_type", "application/json"),
        )
        await emit_observation(
            self._observation_sink,
            ConsumerDeadLetterPublished(
                consumer_name=self._consumer_name,
                task_id=task_id,
                queue_name=retry_infrastructure.dead_letter_queue_name,
                retry_attempt=retry_attempt,
                max_retries=self._retry_policy.max_retries,
                reason=reason,
            ),
        )
        await _persist_dlq_record(
            self._dlq_store,
            consumer_name=self._consumer_name,
            queue_name=retry_infrastructure.dead_letter_queue_name,
            source_queue_name=source_queue_name,
            retry_queue_name=retry_infrastructure.retry_queue_name,
            task_id=task_id,
            correlation_id=getattr(message, "correlation_id", None),
            reason=reason,
            exception_type=exception_type,
            retry_attempt=retry_attempt,
            max_retries=self._retry_policy.max_retries,
            headers=_retry_headers(
                message,
                source_queue_name=source_queue_name,
                retry_attempt=retry_attempt,
                max_retries=self._retry_policy.max_retries,
                reason=reason,
                exception_type=exception_type,
            ),
            content_type=getattr(message, "content_type", "application/json"),
            body=message.body,
            observation_sink=self._observation_sink,
        )


class AggregationWorkerRuntime:
    """Owns one or more aggregation consumer loops outside the FastAPI lifecycle."""

    def __init__(
        self,
        *,
        handler: AggregationHandler,
        shard_groups: list[list[int]],
        topology: RelaynaTopology | None = None,
        rabbitmq: RelaynaRabbitClient | None = None,
        connection_name: str = "relayna-aggregation-runtime",
        consumer_name_prefix: str = "relayna-aggregation-worker",
        prefetch: int | None = None,
        consume_arguments: dict[str, Any] | None = None,
        retry_policy: RetryPolicy | None = None,
        retry_statuses: RetryStatusConfig | None = None,
        consume_timeout_seconds: float | None = 1.0,
        observation_sink: ObservationSink | None = None,
        dlq_store: DLQRecorder | None = None,
    ) -> None:
        if rabbitmq is None and topology is None:
            raise ValueError("Pass rabbitmq=... or topology=... to AggregationWorkerRuntime.")
        self._owns_rabbitmq = rabbitmq is None
        if rabbitmq is not None:
            self._rabbitmq = rabbitmq
        else:
            if topology is None:
                raise ValueError("Pass rabbitmq=... or topology=... to AggregationWorkerRuntime.")
            self._rabbitmq = RelaynaRabbitClient(topology=topology, connection_name=connection_name)
        self._consumers = [
            AggregationConsumer(
                rabbitmq=self._rabbitmq,
                handler=handler,
                shards=shards,
                consumer_name=f"{consumer_name_prefix}-{index}",
                prefetch=prefetch,
                consume_arguments=consume_arguments,
                retry_policy=retry_policy,
                retry_statuses=retry_statuses,
                consume_timeout_seconds=consume_timeout_seconds,
                observation_sink=observation_sink,
                dlq_store=dlq_store,
            )
            for index, shards in enumerate(shard_groups)
        ]
        self._tasks: list[asyncio.Task[None]] = []

    async def start(self) -> None:
        await self._rabbitmq.initialize()
        if self._tasks:
            return
        self._tasks = [
            asyncio.create_task(consumer.run_forever(), name=f"aggregation-{index}")
            for index, consumer in enumerate(self._consumers)
        ]

    async def stop(self) -> None:
        for consumer in self._consumers:
            consumer.stop()
        try:
            if self._tasks:
                await asyncio.wait_for(
                    asyncio.gather(*self._tasks, return_exceptions=True),
                    timeout=5.0,
                )
        except TimeoutError:
            for task in self._tasks:
                task.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
        finally:
            self._tasks = []
            if self._owns_rabbitmq:
                await self._rabbitmq.close()
