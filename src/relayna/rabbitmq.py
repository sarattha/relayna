from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from typing import Any, cast

import aio_pika
from aio_pika import DeliveryMode, Message
from aio_pika.abc import (
    AbstractChannel,
    AbstractExchange,
    AbstractQueue,
    AbstractRobustChannel,
    AbstractRobustConnection,
    AbstractRobustExchange,
)
from pydantic import BaseModel

from .contracts import StatusEventEnvelope, ensure_status_event_id, normalize_event_aliases
from .topology import (
    RelaynaTopology,
    ShardRoutingStrategy,
    SharedTasksSharedStatusTopology,
    TaskIdRoutingStrategy,
)


class RelaynaRabbitClient:
    """Reusable RabbitMQ client for task submission and status streaming."""

    def __init__(
        self,
        topology: RelaynaTopology | None = None,
        *,
        config: RelaynaTopology | None = None,
        connection_name: str = "relayna-robust",
    ) -> None:
        resolved_topology = topology or config
        if resolved_topology is None:
            raise ValueError("Pass topology=... (preferred) or config=... for legacy compatibility.")
        self._topology = resolved_topology
        self._connection_name = connection_name
        self._connection: AbstractRobustConnection | None = None
        self._channel: AbstractRobustChannel | None = None
        self._tasks_exchange: AbstractRobustExchange | None = None
        self._status_exchange: AbstractRobustExchange | None = None
        self._lock = asyncio.Lock()
        self._initialized = False

    @property
    def topology(self) -> RelaynaTopology:
        return self._topology

    @property
    def config(self) -> RelaynaTopology:
        """Backward-compatible alias for older consumers/tests."""
        return self._topology

    async def initialize(self) -> None:
        async with self._lock:
            if self._initialized:
                return

            self._connection = await aio_pika.connect_robust(self._topology.connection_string(self._connection_name))
            self._channel = cast(AbstractRobustChannel, await self._connection.channel())
            await self._channel.set_qos(prefetch_count=self._topology.prefetch_count)
            self._tasks_exchange, self._status_exchange = await self._topology.declare_exchanges(self._channel)
            await self._topology.declare_queues(
                self._channel,
                tasks_exchange=self._tasks_exchange,
                status_exchange=self._status_exchange,
            )
            self._initialized = True

    async def ensure_status_queue(self) -> str:
        await self._ensure_ready()
        if self._channel is None or self._status_exchange is None:
            raise RuntimeError("RabbitMQ connection is not initialized")
        return await self._topology.ensure_status_queue(self._channel, status_exchange=self._status_exchange)

    async def ensure_tasks_queue(self) -> str:
        await self._ensure_ready()
        if self._channel is None or self._tasks_exchange is None:
            raise RuntimeError("RabbitMQ connection is not initialized")
        return await self._topology.ensure_tasks_queue(self._channel, tasks_exchange=self._tasks_exchange)

    async def ensure_aggregation_queue(self, *, shards: Sequence[int], queue_name: str | None = None) -> str:
        await self._ensure_ready()
        if self._channel is None or self._status_exchange is None:
            raise RuntimeError("RabbitMQ connection is not initialized")
        return await self._topology.ensure_aggregation_queue(
            self._channel,
            status_exchange=self._status_exchange,
            shards=shards,
            queue_name=queue_name,
        )

    async def publish_task(self, task: BaseModel | Mapping[str, Any]) -> None:
        await self._ensure_ready()
        if self._tasks_exchange is None:
            raise RuntimeError("Tasks exchange is not initialized")
        task_dict = _to_dict(task)
        message = Message(
            _to_json_bytes(task_dict),
            content_type="application/json",
            delivery_mode=DeliveryMode.PERSISTENT,
            correlation_id=str(task_dict.get("task_id", "")) or None,
            headers={"task_id": str(task_dict.get("task_id", ""))},
        )
        await self._tasks_exchange.publish(message, routing_key=self._topology.task_routing_key(task))

    async def publish_status(self, event: BaseModel | Mapping[str, Any]) -> None:
        payload = self._prepare_status_payload(event)
        await self._publish_status_payload(payload, routing_key=self._topology.status_routing_key(payload))

    async def publish_aggregation_status(self, event: StatusEventEnvelope | Mapping[str, Any]) -> None:
        payload = self._prepare_status_payload(event)
        meta = payload.get("meta")
        if not isinstance(meta, Mapping) or not str(meta.get("parent_task_id") or "").strip():
            raise ValueError("aggregation status event requires meta.parent_task_id")
        payload_meta = dict(meta)
        payload_meta["aggregation_shard"] = self._topology.aggregation_shard(payload)
        payload_meta["aggregation_role"] = "aggregation"
        payload["meta"] = payload_meta
        await self._publish_status_payload(payload, routing_key=self._topology.aggregation_status_routing_key(payload))

    async def acquire_channel(self, prefetch: int = 200) -> AbstractChannel:
        await self._ensure_ready()
        if self._connection is None:
            raise RuntimeError("RabbitMQ robust connection is not available")
        channel = await self._connection.channel()
        await channel.set_qos(prefetch_count=prefetch)
        return channel

    async def ping(self) -> None:
        channel = await self.acquire_channel(prefetch=self._topology.prefetch_count)
        try:
            pass
        finally:
            await channel.close()

    async def close(self) -> None:
        async with self._lock:
            self._initialized = False
            if self._connection and not self._connection.is_closed:
                await self._connection.close()
            self._connection = None
            self._channel = None
            self._tasks_exchange = None
            self._status_exchange = None

    async def _ensure_ready(self) -> None:
        if not self._initialized:
            await self.initialize()

    def _prepare_status_payload(self, event: BaseModel | Mapping[str, Any]) -> dict[str, Any]:
        if isinstance(event, StatusEventEnvelope):
            event_dict = event.as_transport_dict()
        elif isinstance(event, BaseModel):
            event_dict = _to_dict(event)
        else:
            event_dict = normalize_event_aliases(_to_dict(event))

        status = event_dict.get("status")
        if isinstance(status, str):
            status_value = status
        elif status is None:
            status_value = ""
        else:
            status_value = str(getattr(status, "value", status))
            event_dict["status"] = status_value

        task_id = str(event_dict.get("task_id", ""))
        correlation_id = event_dict.get("correlation_id") or task_id
        if correlation_id:
            event_dict["correlation_id"] = str(correlation_id)

        return ensure_status_event_id(event_dict)

    async def _publish_status_payload(self, payload: dict[str, Any], *, routing_key: str) -> None:
        await self._ensure_ready()
        if self._status_exchange is None:
            raise RuntimeError("Status exchange is not initialized")
        task_id = str(payload.get("task_id", ""))
        correlation_id = payload.get("correlation_id")
        message = Message(
            _to_json_bytes(payload),
            content_type="application/json",
            delivery_mode=DeliveryMode.PERSISTENT,
            correlation_id=str(correlation_id) or None,
            headers={"task_id": task_id, "status": str(payload.get("status", ""))},
        )
        await self._status_exchange.publish(message, routing_key=routing_key)


async def declare_stream_queue(
    *,
    channel: AbstractChannel,
    queue_name: str,
    queue_arguments: dict[str, Any] | None,
) -> AbstractQueue:
    return await channel.declare_queue(queue_name, durable=True, arguments=queue_arguments or None)


class DirectQueuePublisher:
    """Publishes messages directly to a queue using the default exchange."""

    def __init__(
        self,
        *,
        amqp_url: str,
        queue_name: str,
        queue_arguments: dict[str, Any] | None = None,
        connection_name: str = "relayna-direct-queue-publisher",
    ) -> None:
        self._topology = SharedTasksSharedStatusTopology(
            rabbitmq_url=amqp_url,
            tasks_exchange="",
            tasks_queue=queue_name,
            tasks_routing_key=queue_name,
            status_exchange="",
            status_queue="",
        )
        self._queue_name = queue_name
        self._queue_arguments = queue_arguments or {}
        self._connection_name = connection_name
        self._connection: AbstractRobustConnection | None = None
        self._channel: AbstractRobustChannel | None = None
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        async with self._lock:
            if self._connection and not self._connection.is_closed and self._channel and not self._channel.is_closed:
                return
            self._connection = await aio_pika.connect_robust(self._topology.connection_string(self._connection_name))
            self._channel = cast(AbstractRobustChannel, await self._connection.channel())
            await self._channel.declare_queue(self._queue_name, durable=True, arguments=self._queue_arguments or None)

    async def publish(self, payload: Mapping[str, Any], *, correlation_id: str | None = None) -> None:
        await self.initialize()
        if self._channel is None:
            raise RuntimeError("Publisher channel is not initialized")
        message = Message(
            _to_json_bytes(payload),
            content_type="application/json",
            delivery_mode=DeliveryMode.PERSISTENT,
            correlation_id=correlation_id,
        )
        await self._channel.default_exchange.publish(message, routing_key=self._queue_name)

    async def close(self) -> None:
        async with self._lock:
            if self._connection and not self._connection.is_closed:
                await self._connection.close()
            self._connection = None
            self._channel = None


def _to_dict(payload: BaseModel | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(payload, BaseModel):
        return cast(dict[str, Any], payload.model_dump(mode="json", exclude_none=True))
    return dict(payload)


def _to_json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


__all__ = [
    "DirectQueuePublisher",
    "RelaynaRabbitClient",
    "ShardRoutingStrategy",
    "TaskIdRoutingStrategy",
    "declare_stream_queue",
]
