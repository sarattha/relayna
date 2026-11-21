from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from ..consumer import RetryPolicy
from ..contracts import StatusEventEnvelope, TaskEnvelope
from .client import SpooltailClient

logger = logging.getLogger(__name__)


class _Record(Protocol):
    event: dict[str, Any]


class _Lease(Protocol):
    record: _Record
    generation: int


class SpooltailTaskContext:
    """Context for a leased task. Terminal status is committed after the handler returns."""

    def __init__(self, client: SpooltailClient, task: TaskEnvelope, *, generation: int, max_retries: int) -> None:
        self._client = client
        self._task_id = task.task_id
        self._service = task.service
        self._terminal: StatusEventEnvelope | None = None
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
            event = StatusEventEnvelope(
                task_id=self._task_id,
                status=status,
                message=message,
                meta=meta or {},
                result=result,
                event_id=event_id,
                service=service if service is not None else self._service,
                correlation_id=self.correlation_id,
            )
        else:
            event = event.model_copy(
                update={"task_id": self._task_id, "correlation_id": event.correlation_id or self.correlation_id}
            )
        if self._terminal is not None:
            raise ValueError("The Spooltail task already has a pending terminal status")
        if event.status.lower() in {"completed", "failed"}:
            self._terminal = event
        else:
            await self._client.publish_status(event)

    def _default_status(self, status: str, message: str | None = None) -> StatusEventEnvelope:
        return StatusEventEnvelope(
            task_id=self._task_id,
            status=status,
            correlation_id=self.correlation_id,
            service=self._service,
            message=message,
        )


class SpooltailTaskConsumer:
    """Processes one lease at a time. Workers sharing a group compete for the same tasks."""

    def __init__(
        self,
        *,
        client: SpooltailClient,
        handler: Callable[[TaskEnvelope, SpooltailTaskContext], Awaitable[None]],
        group: str = "relayna.workers",
        retry_policy: RetryPolicy | None = None,
        lease_ms: int = 30_000,
        idle_retry_seconds: float = 0.25,
    ) -> None:
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
        self._handler = handler
        self._group = group
        self._max_retries = retry_policy.max_retries if retry_policy is not None else 0
        self._retry_delay_ms = retry_policy.delay_ms if retry_policy is not None else 0
        self._lease_ms = lease_ms
        self._idle_retry_seconds = idle_retry_seconds
        self._stop = asyncio.Event()

    def stop(self) -> None:
        """Stop polling after the current delivery finishes."""
        self._stop.set()

    async def run_forever(self) -> None:
        while not self._stop.is_set():
            try:
                if await self.run_once():
                    continue
            except Exception as exc:
                if self._client._sdk is None or not (
                    isinstance(exc, self._client._sdk.StaleLeaseError) or self._client._is_transient(exc)
                ):
                    raise
                logger.warning("Spooltail worker could not finish a broker operation", exc_info=True)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._idle_retry_seconds)
            except TimeoutError:
                pass

    async def run_once(self) -> bool:
        """Poll once and process a delivery. Return False if there was no available work."""
        native = self._client._connection
        lease: _Lease | None = await native.claim(self._client._task_topic, self._group, lease_ms=self._lease_ms)
        if lease is None:
            return False

        actions = self._client._sdk.SettlementAction
        try:
            task = TaskEnvelope.model_validate(lease.record.event)
            if task.priority is not None:
                raise ValueError("Spooltail does not support task priority")
        except ValueError:
            logger.warning("Dead-lettering invalid Spooltail task")
            await native.settle(lease, actions.DEAD_LETTER)
            return True

        context = SpooltailTaskContext(self._client, task, generation=lease.generation, max_retries=self._max_retries)
        terminal = await self._process(lease, task, context)
        if terminal is None:
            await native.settle(lease, actions.RETRY, delay_ms=self._retry_delay_ms)
        else:
            payload = terminal.as_transport_dict()
            try:
                await native.complete(lease, self._client._status_topic, payload)
            except Exception as exc:
                if not self._client._is_transient(exc):
                    raise
                # An exact replay is safe even if the first request already acknowledged the lease.
                await native.complete(lease, self._client._status_topic, payload)
        return True

    async def _process(
        self, lease: _Lease, task: TaskEnvelope, context: SpooltailTaskContext
    ) -> StatusEventEnvelope | None:
        if context.retry_attempt > self._max_retries:
            return context._default_status("failed", "Task exhausted its delivery attempts")

        handler = asyncio.create_task(self._invoke(task, context))
        renewal = asyncio.create_task(self._renew(lease))
        try:
            done, _ = await asyncio.wait((handler, renewal), return_when=asyncio.FIRST_COMPLETED)
            if renewal in done:
                await renewal
            await handler
        except _HandlerFailed as exc:
            if context.retry_attempt < self._max_retries:
                return None
            logger.warning("Spooltail task %s exhausted its retries", context._task_id, exc_info=exc.__cause__)
            return context._default_status("failed", "Task handler failed")
        finally:
            for pending in (handler, renewal):
                if not pending.done():
                    pending.cancel()
            await asyncio.gather(handler, renewal, return_exceptions=True)

        return context._terminal or context._default_status("completed")

    async def _invoke(self, task: TaskEnvelope, context: SpooltailTaskContext) -> None:
        try:
            await self._handler(task, context)
        except Exception as exc:
            if self._client._is_transient(exc):
                raise
            raise _HandlerFailed from exc

    async def _renew(self, lease: _Lease) -> None:
        native = self._client._connection
        while True:
            await asyncio.sleep(self._lease_ms / 3000)
            await native.settle(lease, self._client._sdk.SettlementAction.RENEW, delay_ms=self._lease_ms)


class _HandlerFailed(Exception):
    pass
