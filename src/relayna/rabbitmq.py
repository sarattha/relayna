from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol, cast

import aio_pika
from aio_pika import DeliveryMode, ExchangeType, Message
from aio_pika.abc import (
    AbstractChannel,
    AbstractExchange,
    AbstractQueue,
    AbstractRobustChannel,
    AbstractRobustConnection,
    AbstractRobustExchange,
)
from pydantic import BaseModel

from .config import RelaynaTopologyConfig
from .contracts import normalize_event_aliases


class RoutingStrategy(Protocol):
    def task_routing_key(self, task: BaseModel | Mapping[str, Any]) -> str: ...

    def status_routing_key(self, event: BaseModel | Mapping[str, Any]) -> str: ...


class TaskIdRoutingStrategy:
    """Default route strategy: tasks to static key, statuses keyed by task_id."""

    def __init__(self, tasks_routing_key: str) -> None:
        self._tasks_routing_key = tasks_routing_key

    def task_routing_key(self, task: BaseModel | Mapping[str, Any]) -> str:
        return self._tasks_routing_key

    def status_routing_key(self, event: BaseModel | Mapping[str, Any]) -> str:
        data = _to_dict(event)
        normalized = normalize_event_aliases(data)
        task_id = str(normalized.get("task_id", ""))
        if not task_id:
            raise ValueError("status event missing task_id")
        return task_id


class ShardRoutingStrategy(TaskIdRoutingStrategy):
    """Routes status events to deterministic shards (e.g. agg.<shard>)."""

    def __init__(
        self,
        tasks_routing_key: str,
        *,
        shard_count: int,
        routing_prefix: str = "agg",
        key_extractor: Callable[[dict[str, Any]], str] | None = None,
    ) -> None:
        super().__init__(tasks_routing_key)
        self._shard_count = max(1, int(shard_count))
        self._routing_prefix = routing_prefix
        self._key_extractor = key_extractor

    def status_routing_key(self, event: BaseModel | Mapping[str, Any]) -> str:
        data = _to_dict(event)
        normalized = normalize_event_aliases(data)
        if self._key_extractor is not None:
            key = self._key_extractor(normalized)
        else:
            meta = normalized.get("meta")
            parent_task_id = meta.get("parent_task_id") if isinstance(meta, Mapping) else None
            key = str(parent_task_id or normalized.get("task_id") or "")
        if not key:
            raise ValueError("status event missing shard key")
        shard = int(hashlib.sha256(key.encode("utf-8")).hexdigest(), 16) % self._shard_count
        return f"{self._routing_prefix}.{shard}"


class RelaynaRabbitClient:
    """Reusable RabbitMQ client for task submission and status streaming."""

    def __init__(
        self,
        config: RelaynaTopologyConfig,
        *,
        routing_strategy: RoutingStrategy | None = None,
        connection_name: str = "relayna-robust",
        extra_topology_declarer: Callable[
            [AbstractRobustChannel, AbstractRobustExchange], Awaitable[None]
        ]
        | None = None,
    ) -> None:
        self._config = config
        self._routing = routing_strategy or TaskIdRoutingStrategy(config.tasks_routing_key)
        self._connection_name = connection_name
        self._extra_topology_declarer = extra_topology_declarer
        self._connection: AbstractRobustConnection | None = None
        self._channel: AbstractRobustChannel | None = None
        self._tasks_exchange: AbstractRobustExchange | None = None
        self._status_exchange: AbstractRobustExchange | None = None
        self._lock = asyncio.Lock()
        self._initialized = False

    @property
    def config(self) -> RelaynaTopologyConfig:
        return self._config

    async def initialize(self) -> None:
        async with self._lock:
            if self._initialized:
                return

            self._connection = await aio_pika.connect_robust(self._config.connection_string(self._connection_name))
            self._channel = cast(AbstractRobustChannel, await self._connection.channel())
            await self._channel.set_qos(prefetch_count=self._config.prefetch_count)

            if self._config.dead_letter_exchange:
                await self._channel.declare_exchange(
                    self._config.dead_letter_exchange,
                    ExchangeType.FANOUT,
                    durable=True,
                )

            self._tasks_exchange = await self._channel.declare_exchange(
                self._config.tasks_exchange,
                ExchangeType.DIRECT,
                durable=True,
            )
            self._status_exchange = await self._channel.declare_exchange(
                self._config.status_exchange,
                ExchangeType.TOPIC,
                durable=True,
            )

            if self._channel is None or self._status_exchange is None:
                raise RuntimeError("RabbitMQ connection is not initialized")
            queue = await self._channel.declare_queue(
                self._config.status_queue,
                durable=True,
                arguments=self._config.status_queue_arguments() or None,
            )
            await queue.bind(cast(AbstractExchange, self._status_exchange), routing_key="#")
            
            task_queue = await self._channel.declare_queue(
                self._config.tasks_queue,
                durable=True,
                arguments=self._config.task_queue_arguments() or None,
            )
            await task_queue.bind(self._tasks_exchange, routing_key=self._config.tasks_routing_key)

            if self._extra_topology_declarer:
                await self._extra_topology_declarer(self._channel, self._status_exchange)

            self._initialized = True

    async def ensure_status_queue(self) -> str:
        await self._ensure_ready()
        if self._channel is None or self._status_exchange is None:
            raise RuntimeError("RabbitMQ connection is not initialized")
        queue = await self._channel.declare_queue(
            self._config.status_queue,
            durable=True,
            arguments=self._config.status_queue_arguments() or None,
        )
        await queue.bind(cast(AbstractExchange, self._status_exchange), routing_key="#")
        return queue.name

    async def ensure_tasks_queue(self) -> str:
        await self._ensure_ready()
        if self._channel is None or self._tasks_exchange is None:
            raise RuntimeError("RabbitMQ connection is not initialized")
        queue = await self._channel.declare_queue(
            self._config.tasks_queue,
            durable=True,
            arguments=self._config.task_queue_arguments() or None,
        )
        await queue.bind(self._tasks_exchange, routing_key=self._config.tasks_routing_key)
        return queue.name

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
        await self._tasks_exchange.publish(message, routing_key=self._routing.task_routing_key(task))

    async def publish_status(self, event: BaseModel | Mapping[str, Any]) -> None:
        await self._ensure_ready()
        if self._status_exchange is None:
            raise RuntimeError("Status exchange is not initialized")
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
        message = Message(
            _to_json_bytes(event_dict),
            content_type="application/json",
            delivery_mode=DeliveryMode.PERSISTENT,
            correlation_id=str(correlation_id) or None,
            headers={"task_id": task_id, "status": status_value},
        )
        await self._status_exchange.publish(message, routing_key=self._routing.status_routing_key(event))

    async def acquire_channel(self, prefetch: int = 200) -> AbstractChannel:
        await self._ensure_ready()
        if self._connection is None:
            raise RuntimeError("RabbitMQ robust connection is not available")
        channel = await self._connection.channel()
        await channel.set_qos(prefetch_count=prefetch)
        return channel

    async def ping(self) -> None:
        channel = await self.acquire_channel(prefetch=self._config.prefetch_count)
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


def _to_dict(payload: BaseModel | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(payload, BaseModel):
        return cast(dict[str, Any], payload.model_dump(mode="json", exclude_none=True))
    return dict(payload)


def _to_json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


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
        self._amqp_url = amqp_url
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
            separator = "&" if "?" in self._amqp_url else "?"
            connection_string = f"{self._amqp_url}{separator}name={self._connection_name}"
            self._connection = await aio_pika.connect_robust(connection_string)
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
