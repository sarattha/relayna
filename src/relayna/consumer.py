from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from pydantic import ValidationError

from .contracts import StatusEventEnvelope, TaskEnvelope
from .observability import (
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
from .rabbitmq import RelaynaRabbitClient
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
class TaskContext:
    rabbitmq: RelaynaRabbitClient
    consumer_name: str
    raw_payload: dict[str, Any]
    correlation_id: str | None
    delivery_tag: int | None
    redelivered: bool
    _task_id: str = field(repr=False)

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
        idle_retry_seconds: float = 2.0,
        observation_sink: ObservationSink | None = None,
    ) -> None:
        self._rabbitmq = rabbitmq
        self._handler = handler
        self._consumer_name = consumer_name
        self._prefetch = prefetch
        self._consume_arguments = consume_arguments or {}
        self._failure_action = failure_action
        self._lifecycle_statuses = lifecycle_statuses or LifecycleStatusConfig()
        self._idle_retry_seconds = idle_retry_seconds
        self._observation_sink = observation_sink
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run_forever(self) -> None:
        queue_name: str | None = None
        prefetch = self._prefetch or self._rabbitmq.topology.prefetch_count
        started = False

        while not self._stop.is_set():
            channel = None
            try:
                if queue_name is None:
                    queue_name = await self._rabbitmq.ensure_tasks_queue()
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
                        await self._handle_message(message)
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

    async def _handle_message(self, message: Any) -> None:
        try:
            payload = json.loads(message.body.decode("utf-8", errors="replace"))
        except Exception:
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
            return

        await emit_observation(
            self._observation_sink,
            TaskMessageReceived(
                consumer_name=self._consumer_name,
                task_id=_coerce_task_id(payload),
                delivery_tag=getattr(message, "delivery_tag", None),
                redelivered=bool(getattr(message, "redelivered", False)),
            ),
        )

        try:
            task = TaskEnvelope.model_validate(payload)
        except ValidationError:
            await message.reject(requeue=False)
            await emit_observation(
                self._observation_sink,
                TaskMessageRejected(
                    consumer_name=self._consumer_name,
                    task_id=_coerce_task_id(payload),
                    requeue=False,
                    reason="invalid_envelope",
                ),
            )
            return

        context = TaskContext(
            rabbitmq=self._rabbitmq,
            consumer_name=self._consumer_name,
            raw_payload=dict(payload),
            correlation_id=task.correlation_id or getattr(message, "correlation_id", None),
            delivery_tag=getattr(message, "delivery_tag", None),
            redelivered=bool(getattr(message, "redelivered", False)),
            _task_id=task.task_id,
        )

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
            await message.ack()
            await emit_observation(
                self._observation_sink,
                TaskMessageAcked(consumer_name=self._consumer_name, task_id=task.task_id),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failed_message = "Task processing failed."
            if self._lifecycle_statuses.include_error_message:
                text = str(exc).strip()
                if text:
                    failed_message = text
            await self._publish_lifecycle_status(
                context,
                status=self._lifecycle_statuses.failed_status,
                message=failed_message,
            )
            requeue = self._failure_action is FailureAction.REQUEUE
            await emit_observation(
                self._observation_sink,
                TaskHandlerFailed(
                    consumer_name=self._consumer_name,
                    task_id=task.task_id,
                    exception_type=type(exc).__name__,
                    requeue=requeue,
                ),
            )
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


def _coerce_task_id(payload: Any) -> str | None:
    if isinstance(payload, dict):
        task_id = payload.get("task_id")
        if isinstance(task_id, str):
            return task_id
    return None


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
        idle_retry_seconds: float = 2.0,
    ) -> None:
        self._rabbitmq = rabbitmq
        self._handler = handler
        self._shards = list(shards)
        self._queue_name = queue_name
        self._consumer_name = consumer_name
        self._prefetch = prefetch
        self._consume_arguments = consume_arguments or {}
        self._idle_retry_seconds = idle_retry_seconds
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run_forever(self) -> None:
        queue_name: str | None = self._queue_name
        prefetch = self._prefetch or self._rabbitmq.topology.prefetch_count

        while not self._stop.is_set():
            channel = None
            try:
                queue_name = await self._rabbitmq.ensure_aggregation_queue(
                    shards=self._shards,
                    queue_name=queue_name,
                )
                channel = await self._rabbitmq.acquire_channel(prefetch=prefetch)
                queue = await channel.declare_queue(
                    queue_name,
                    durable=True,
                    arguments=self._rabbitmq.topology.aggregation_queue_arguments() or None,
                )
                async with queue.iterator(arguments=self._consume_arguments or None, timeout=1.0) as iterator:
                    async for message in iterator:
                        try:
                            payload = json.loads(message.body.decode("utf-8", errors="replace"))
                            event = StatusEventEnvelope.model_validate(payload)
                        except Exception:
                            await message.reject(requeue=False)
                            continue

                        context = TaskContext(
                            rabbitmq=self._rabbitmq,
                            consumer_name=self._consumer_name,
                            raw_payload=dict(payload),
                            correlation_id=event.correlation_id or getattr(message, "correlation_id", None),
                            delivery_tag=getattr(message, "delivery_tag", None),
                            redelivered=bool(getattr(message, "redelivered", False)),
                            _task_id=event.task_id,
                        )
                        try:
                            await self._handler(event, context)
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            await message.reject(requeue=False)
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
