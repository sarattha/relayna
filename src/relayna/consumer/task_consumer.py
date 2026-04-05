from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from ..contracts import BatchTaskEnvelope, ContractAliasConfig, StatusEventEnvelope, TaskEnvelope, is_batch_task_payload
from ..dlq import DLQRecorder
from ..observability import (
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
from ..rabbitmq import RelaynaRabbitClient, RetryInfrastructure
from ..topology import RoutedTasksSharedStatusTopology
from .context import (
    AggregationHandler,
    FailureAction,
    LifecycleStatusConfig,
    RetryPolicy,
    RetryStatusConfig,
    TaskContext,
    TaskHandler,
    _coerce_task_id,
    _failure_message,
    _header_string,
    _manual_retry_count,
    _manual_retry_meta_from_status,
    _ManualRetryRequested,
    _merge_batch_retry_headers,
    _message_headers,
    _normalize_batch_payload,
    _normalize_payload,
    _persist_dlq_record,
    _retry_attempt,
    _retry_headers,
    _to_json_bytes,
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
                    arguments=self._consume_arguments or None, timeout=self._consume_timeout_seconds
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
        self, message: Any, *, source_queue_name: str, retry_infrastructure: RetryInfrastructure | None
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
            task=task, raw_payload=dict(normalized_payload), message=message, source_queue_name=source_queue_name
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
            self._observation_sink, TaskMessageAcked(consumer_name=self._consumer_name, task_id=None)
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
        from .context import _header_int

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
                context, status=self._lifecycle_statuses.processing_status, message="Task processing started."
            )
            try:
                await self._handler(task, context)
            except _ManualRetryRequested:
                pass
            if context._manual_retry_request is not None:
                await self._publish_manual_retry_request(context, context._manual_retry_request)
                return True
            await self._publish_lifecycle_status(
                context, status=self._lifecycle_statuses.completed_status, message="Task processing completed."
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
                    context, status=self._lifecycle_statuses.failed_status, message=failed_message
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

    async def _publish_manual_retry_request(self, context: TaskContext, request) -> None:
        try:
            await context.publish_status(
                status="manual_retrying", message=request.status_message, meta=request.status_meta
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
                    consumer_name=self._consumer_name, task_id=context._task_id, status=status
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
                queue_name = await self._rabbitmq.ensure_aggregation_queue(shards=self._shards, queue_name=queue_name)
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
                    arguments=self._consume_arguments or None, timeout=self._consume_timeout_seconds
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
                                    context, next_attempt=next_attempt, max_retries=max_retries, meta=event.meta
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
        self, context: TaskContext, *, next_attempt: int, max_retries: int, meta: Mapping[str, Any]
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
        self, context: TaskContext, exc: Exception, *, meta: Mapping[str, Any]
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


__all__ = [
    "AggregationConsumer",
    "AggregationHandler",
    "FailureAction",
    "LifecycleStatusConfig",
    "RetryPolicy",
    "RetryStatusConfig",
    "TaskConsumer",
    "TaskHandler",
]
