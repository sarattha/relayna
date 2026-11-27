from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from ..consumer import RetryPolicy
from ..contracts import StatusEventEnvelope, TaskEnvelope

if TYPE_CHECKING:
    from relayna_spooltail import Lease, SpooltailClient  # ty: ignore[unresolved-import]

logger = logging.getLogger(__name__)


class _SpooltailStatusEvent(StatusEventEnvelope):
    attempt_id: str


class SpooltailTaskContext:
    """Context for a leased task. Terminal status is committed after the handler returns."""

    def __init__(
        self,
        client: SpooltailClient,
        status_topic: str,
        task: TaskEnvelope,
        *,
        generation: int,
        max_retries: int,
        cancellation_enabled: bool = False,
    ) -> None:
        self._client = client
        self._status_topic = status_topic
        self._task_id = task.task_id
        self._service = task.service
        self._terminal: StatusEventEnvelope | None = None
        self._cancellation_enabled = cancellation_enabled
        self._execution_meta: dict[str, Any] = {}
        self.attempt_id = uuid4().hex
        self.correlation_id = task.correlation_id or task.task_id
        self.redelivered = generation > 1
        # Explicit retries advance the generation twice, while lease expiry advances it once.
        self.retry_attempt = (generation - 1) // 2
        self.max_retries = max_retries

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
        if (event is None) == (status is None):
            raise ValueError("Pass either a StatusEventEnvelope or status keyword fields")
        if event is None:
            assert status is not None
            event = _SpooltailStatusEvent(
                task_id=self._task_id,
                status=status,
                message=message,
                meta=meta or {},
                result=result,
                event_id=event_id,
                service=service if service is not None else self._service,
                correlation_id=self.correlation_id,
                attempt_id=self.attempt_id,
            )
        else:
            event = event.model_copy(
                update={
                    "task_id": self._task_id,
                    "correlation_id": event.correlation_id or self.correlation_id,
                    "attempt_id": self.attempt_id,
                }
            )
        if self._terminal is not None:
            raise ValueError("The Spooltail task already has a pending terminal status")
        kind = event.status.lower()
        if kind in {"completed", "failed"} or (kind == "cancelled" and self._cancellation_enabled):
            self._terminal = event
        else:
            await self._client.publish(self._status_topic, event.as_transport_dict())

    def _default_status(self, status: str, message: str | None = None) -> StatusEventEnvelope:
        return _SpooltailStatusEvent(
            task_id=self._task_id,
            status=status,
            correlation_id=self.correlation_id,
            service=self._service,
            message=message,
            meta=self._execution_meta,
            attempt_id=self.attempt_id,
        )


class SpooltailTaskConsumer:
    """Processes one lease at a time. Workers sharing a group compete for the same tasks."""

    def __init__(
        self,
        *,
        client: SpooltailClient,
        handler: Callable[[TaskEnvelope, SpooltailTaskContext], Awaitable[None]] | None = None,
        profile: Callable[[TaskEnvelope, SpooltailTaskContext], Awaitable[None]] | None = None,
        task_topic: str = "relayna.tasks",
        status_topic: str = "relayna.status",
        group: str = "relayna.workers",
        retry_policy: RetryPolicy | None = None,
        lease_ms: int = 30_000,
        idle_retry_seconds: float = 0.25,
        cancel_topic: str | None = None,
        cancel_check_seconds: float = 0.25,
    ) -> None:
        selected_handler = handler if handler is not None else profile
        if selected_handler is None or (handler is not None and profile is not None):
            raise ValueError("Configure exactly one Spooltail handler or execution profile")
        if not task_topic or not status_topic or task_topic == status_topic:
            raise ValueError("Spooltail requires distinct task and status topics")
        if cancel_topic is not None and (not cancel_topic or cancel_topic in {task_topic, status_topic}):
            raise ValueError("Spooltail requires a distinct cancellation topic")
        if cancel_check_seconds <= 0:
            raise ValueError("cancel_check_seconds must be positive")
        if not group:
            raise ValueError("Spooltail worker group must not be empty")
        if not 1 <= lease_ms <= 3_600_000:
            raise ValueError("Spooltail lease_ms must be 1..3600000")
        if idle_retry_seconds <= 0:
            raise ValueError("idle_retry_seconds must be positive")
        if retry_policy is not None:
            if retry_policy.max_retries < 0 or not 0 <= retry_policy.delay_ms <= 3_600_000:
                raise ValueError("Spooltail requires nonnegative max_retries and delay_ms in 0..3600000")
            if retry_policy.retry_priority_step is not None:
                raise ValueError("Spooltail does not support retry priority")

        self._client = client
        self._handler = selected_handler
        self._task_topic = task_topic
        self._status_topic = status_topic
        self._group = group
        self._max_retries = retry_policy.max_retries if retry_policy is not None else 0
        self._retry_delay_ms = retry_policy.delay_ms if retry_policy is not None else 0
        self._lease_ms = lease_ms
        self._idle_retry_seconds = idle_retry_seconds
        self._cancel_topic = cancel_topic
        self._cancel_check_seconds = cancel_check_seconds
        self._stop = asyncio.Event()

    def stop(self) -> None:
        """Stop polling after the current delivery finishes."""
        self._stop.set()

    async def run_forever(self) -> None:
        from relayna_spooltail import StaleLeaseError  # ty: ignore[unresolved-import]

        while not self._stop.is_set():
            try:
                if await self.run_once():
                    continue
            except Exception as exc:
                if not isinstance(exc, StaleLeaseError) and not _is_transient(exc):
                    raise
                logger.warning("Spooltail worker could not finish a broker operation", exc_info=True)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._idle_retry_seconds)
            except TimeoutError:
                pass

    async def run_once(self) -> bool:
        """Poll once and process a delivery. Return False if there was no available work."""
        from relayna_spooltail import SettlementAction  # ty: ignore[unresolved-import]

        lease = await self._client.claim(self._task_topic, self._group, lease_ms=self._lease_ms)
        if lease is None:
            return False

        try:
            task = TaskEnvelope.model_validate(lease.record.event)
            if task.priority is not None:
                raise ValueError("Spooltail does not support task priority")
        except ValueError:
            logger.warning("Dead-lettering invalid Spooltail task")
            await self._client.settle(lease, SettlementAction.DEAD_LETTER)
            return True

        context = SpooltailTaskContext(
            self._client,
            self._status_topic,
            task,
            generation=lease.generation,
            max_retries=self._max_retries,
            cancellation_enabled=self._cancel_topic is not None,
        )
        terminal = await self._process(lease, task, context)
        if terminal is None:
            await self._client.settle(lease, SettlementAction.RETRY, delay_ms=self._retry_delay_ms)
        else:
            payload = terminal.as_transport_dict()
            if terminal.status.lower() == "failed":
                payload["meta"] = terminal.meta | {
                    "spooltail_retry": {"attempt": context.retry_attempt, "max_retries": context.max_retries}
                }
            try:
                await self._client.complete(lease, self._status_topic, payload)
            except Exception as exc:
                if not _is_transient(exc):
                    raise
                # An exact replay is safe even if the first request already acknowledged the lease.
                await self._client.complete(lease, self._status_topic, payload)
        return True

    async def _process(
        self, lease: Lease, task: TaskEnvelope, context: SpooltailTaskContext
    ) -> StatusEventEnvelope | None:
        if self._cancel_topic is not None and await self._client.latest(self._cancel_topic, task.task_id) is not None:
            return context._default_status("cancelled")
        if context.retry_attempt > self._max_retries:
            return context._default_status("failed", "Task exhausted its delivery attempts")

        handler = asyncio.create_task(self._invoke(task, context))
        renewal = asyncio.create_task(self._renew(lease))
        active = [handler, renewal]
        cancellation = None
        if self._cancel_topic is not None:
            cancellation = asyncio.create_task(self._wait_for_cancellation(self._cancel_topic, task.task_id))
            active.append(cancellation)
        try:
            done, _ = await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
            if renewal in done:
                await renewal
            if cancellation is not None and cancellation in done:
                await cancellation
                handler.cancel()
                # Let the handler clean up while the work lease continues to renew.
                await asyncio.gather(handler, return_exceptions=True)
                if renewal.done():
                    await renewal
                return context._default_status("cancelled")
            await handler
        except _HandlerFailed as exc:
            if context.retry_attempt < self._max_retries:
                return None
            logger.warning("Spooltail task %s exhausted its retries", context._task_id, exc_info=exc.__cause__)
            return context._default_status("failed", "Task handler failed")
        finally:
            for pending in active:
                if not pending.done():
                    pending.cancel()
            await asyncio.gather(*active, return_exceptions=True)

        return context._terminal or context._default_status("completed")

    async def _wait_for_cancellation(self, topic: str, task_id: str) -> None:
        while await self._client.latest(topic, task_id) is None:
            await asyncio.sleep(self._cancel_check_seconds)

    async def _invoke(self, task: TaskEnvelope, context: SpooltailTaskContext) -> None:
        try:
            await self._handler(task, context)
        except Exception as exc:
            if _is_transient(exc):
                raise
            raise _HandlerFailed from exc

    async def _renew(self, lease: Lease) -> None:
        from relayna_spooltail import SettlementAction  # ty: ignore[unresolved-import]

        while True:
            await asyncio.sleep(self._lease_ms / 3000)
            await self._client.settle(lease, SettlementAction.RENEW, delay_ms=self._lease_ms)


def _is_transient(exc: Exception) -> bool:
    from grpc import StatusCode  # ty: ignore[unresolved-import]
    from grpc.aio import AioRpcError  # ty: ignore[unresolved-import]

    return isinstance(exc, AioRpcError) and exc.code() in {StatusCode.DEADLINE_EXCEEDED, StatusCode.UNAVAILABLE}


class _HandlerFailed(Exception):
    pass
