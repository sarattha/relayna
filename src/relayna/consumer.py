from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from pydantic import ValidationError

from .contracts import StatusEventEnvelope, TaskEnvelope
from .rabbitmq import RelaynaRabbitClient


class TaskHandler(Protocol):
    async def __call__(self, task: TaskEnvelope, context: TaskContext) -> None: ...


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
    ) -> None:
        self._rabbitmq = rabbitmq
        self._handler = handler
        self._consumer_name = consumer_name
        self._prefetch = prefetch
        self._consume_arguments = consume_arguments or {}
        self._failure_action = failure_action
        self._lifecycle_statuses = lifecycle_statuses or LifecycleStatusConfig()
        self._idle_retry_seconds = idle_retry_seconds
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run_forever(self) -> None:
        queue_name: str | None = None
        prefetch = self._prefetch or self._rabbitmq.config.prefetch_count

        while not self._stop.is_set():
            channel = None
            try:
                if queue_name is None:
                    queue_name = await self._rabbitmq.ensure_tasks_queue()

                channel = await self._rabbitmq.acquire_channel(prefetch=prefetch)
                queue = await channel.declare_queue(
                    queue_name,
                    durable=True,
                    arguments=self._rabbitmq.config.task_queue_arguments() or None,
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

    async def _handle_message(self, message: Any) -> None:
        try:
            payload = json.loads(message.body.decode("utf-8", errors="replace"))
        except Exception:
            await message.reject(requeue=False)
            return

        try:
            task = TaskEnvelope.model_validate(payload)
        except ValidationError:
            await message.reject(requeue=False)
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
            await message.reject(requeue=self._failure_action is FailureAction.REQUEUE)

    async def _publish_lifecycle_status(self, context: TaskContext, *, status: str, message: str) -> None:
        if not self._lifecycle_statuses.enabled:
            return
        try:
            await context.publish_status(status=status, message=message)
        except asyncio.CancelledError:
            raise
        except Exception:
            return
