from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import aclosing, asynccontextmanager
from importlib import import_module
from types import TracebackType
from typing import Any, Self

from fastapi import APIRouter, FastAPI

from ..api.status_routes import _create_status_router
from ..contracts import StatusEventEnvelope, TaskEnvelope, normalize_contract_aliases
from ..status.stream import _sse_event


class SpooltailClient:
    """Publishes Relayna tasks and reads status through Spooltail's native gRPC client."""

    def __init__(
        self,
        target: str,
        *,
        task_topic: str = "relayna.tasks",
        status_topic: str = "relayna.status",
        tls: bool = True,
        root_certificates: bytes | None = None,
        timeout: float = 5.0,
    ) -> None:
        if not task_topic or not status_topic or task_topic == status_topic:
            raise ValueError("Spooltail requires distinct task and status topics")
        self._target = target
        self._task_topic = task_topic
        self._status_topic = status_topic
        self._tls = tls
        self._root_certificates = root_certificates
        self._timeout = timeout
        self._lock = asyncio.Lock()
        # The native distribution is installed separately for this backend.
        self._native: Any = None
        self._sdk: Any = None

    @property
    def _connection(self) -> Any:
        if self._native is None:
            raise RuntimeError("Initialize the Spooltail client or enter its async context before use")
        return self._native

    async def initialize(self) -> None:
        async with self._lock:
            if self._native is not None:
                return

            try:
                self._sdk = import_module("relayna_spooltail")
            except ModuleNotFoundError as exc:
                if exc.name != "relayna_spooltail":
                    raise
                raise RuntimeError("Install the optional relayna-spooltail Python SDK to use Spooltail") from exc

            native = self._sdk.SpooltailClient(
                self._target, tls=self._tls, root_certificates=self._root_certificates, timeout=self._timeout
            )
            try:
                await native.ping()
            except BaseException:
                await native.close()
                raise
            self._native = native

    async def close(self) -> None:
        async with self._lock:
            native, self._native = self._native, None
            if native is not None:
                await native.close()

    async def __aenter__(self) -> Self:
        await self.initialize()
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        await self.close()

    @asynccontextmanager
    async def lifespan(self, app: FastAPI) -> AsyncIterator[None]:
        """Own this client's connection for a FastAPI application's lifespan."""
        async with self:
            yield

    async def publish_task(self, task: TaskEnvelope | Mapping[str, Any]) -> None:
        """Submit one task. Reuse the same envelope to retry an ambiguous publish."""
        envelope = (
            task
            if isinstance(task, TaskEnvelope)
            else TaskEnvelope.model_validate(normalize_contract_aliases(task, drop_aliases=True))
        )
        if envelope.priority is not None:
            raise ValueError("Spooltail does not support task priority")
        payload = envelope.model_dump(mode="json", exclude_none=True)
        payload.setdefault("event_id", "task")
        await self._connection.publish(self._task_topic, payload)

    async def publish_status(self, event: StatusEventEnvelope | Mapping[str, Any]) -> None:
        """Append a status independently of a worker's lease."""
        envelope = (
            event
            if isinstance(event, StatusEventEnvelope)
            else StatusEventEnvelope.model_validate(normalize_contract_aliases(event, drop_aliases=True))
        )
        await self._connection.publish(self._status_topic, envelope.as_transport_dict())

    async def get_latest(self, task_id: str) -> dict[str, Any] | None:
        record = await self._connection.latest(self._status_topic, task_id)
        return record.event if record is not None else None

    async def get_history(self, task_id: str, *, after: str | None = None) -> list[dict[str, Any]]:
        """Return one history page, using the last event ID to request the next page."""
        records = await self._connection.history(self._status_topic, task_id, after=after)
        return [record.event for record in records]

    async def stream(self, task_id: str, *, last_event_id: str | None = None) -> AsyncIterator[bytes]:
        """Stream SSE frames with native exclusive event ID resume."""
        subscription = self._connection.subscribe(self._status_topic, task_id, after=last_event_id)
        try:
            async with aclosing(subscription):
                async for delivery in subscription:
                    yield _sse_event(delivery.data, event_type=delivery.kind)
        except self._sdk.CursorGapError as exc:
            yield _sse_event({"earliest_event_id": exc.earliest_event_id}, event_type="gap")
        except self._sdk.SubscriptionError as exc:
            yield _sse_event({"error": str(exc)}, event_type="error")

    def status_router(self) -> APIRouter:
        """Create the standard latest-status and SSE routes for this client."""
        return _create_status_router(sse_stream=self, latest_status_store=self)

    @staticmethod
    def _is_transient(exc: Exception) -> bool:
        grpc: Any = import_module("grpc")
        return isinstance(exc, grpc.aio.AioRpcError) and exc.code() in {
            grpc.StatusCode.DEADLINE_EXCEEDED,
            grpc.StatusCode.UNAVAILABLE,
        }
