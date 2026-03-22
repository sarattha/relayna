from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from pydantic import ValidationError

from .contracts import (
    BatchTaskEnvelope,
    ContractAliasConfig,
    StatusEventEnvelope,
    TaskEnvelope,
    is_batch_task_payload,
    normalize_contract_aliases,
)
from .dlq import DLQRecorder, build_dlq_record
from .observability import (
    ConsumerDeadLetterPublished,
    ConsumerRetryScheduled,
    ObservationSink,
    TaskConsumerLoopError,
    TaskConsumerStarted,
    TaskHandlerFailed,
    TaskLifecycleStatusPublished,
    TaskMessageAcked,
    TaskMessageReceived,
    TaskMessageRejected,
    emit_observation,
)
from .rabbitmq import RelaynaRabbitClient, RetryInfrastructure
from .topology import RelaynaTopology


class TaskHandler(Protocol):
    async def __call__(self, task: TaskEnvelope, context: TaskContext) -> None: ...


class AggregationHandler(Protocol):
    async def __call__(self, event: StatusEventEnvelope, context: TaskContext) -> None: ...


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
                meta=meta or {},
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

        payload_meta = dict(meta or {})
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
                async with queue.iterator(arguments=self._consume_arguments or None, timeout=1.0) as iterator:
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
            await self._handler(task, context)
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
                failed_message = _failure_message(exc, include_error_message=self._lifecycle_statuses.include_error_message)
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
        )


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


def _header_int(headers: Mapping[str, Any], key: str) -> int | None:
    value = headers.get(key)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _merge_batch_retry_headers(context: TaskContext, headers: Mapping[str, Any] | None) -> dict[str, Any]:
    merged = dict(headers or {})
    if context.batch_id is not None:
        merged["batch_id"] = context.batch_id
    if context.batch_index is not None:
        merged["batch_index"] = context.batch_index
    if context.batch_size is not None:
        merged["batch_size"] = context.batch_size
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
    except Exception:
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
                async with queue.iterator(arguments=self._consume_arguments or None, timeout=1.0) as iterator:
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
        observation_sink: ObservationSink | None = None,
        dlq_store: DLQRecorder | None = None,
    ) -> None:
        if rabbitmq is None and topology is None:
            raise ValueError("Pass rabbitmq=... or topology=... to AggregationWorkerRuntime.")
        self._owns_rabbitmq = rabbitmq is None
        self._rabbitmq = rabbitmq or RelaynaRabbitClient(topology=topology, connection_name=connection_name)
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
