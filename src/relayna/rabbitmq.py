from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast

import aio_pika
from aio_pika import DeliveryMode, Message
from aio_pika.abc import (
    AbstractChannel,
    AbstractQueue,
    AbstractRobustChannel,
    AbstractRobustConnection,
    AbstractRobustExchange,
)
from pydantic import BaseModel

from .contracts import (
    BatchTaskEnvelope,
    ContractAliasConfig,
    StatusEventEnvelope,
    TaskEnvelope,
    WorkflowEnvelope,
    ensure_status_event_id,
    normalize_contract_aliases,
)
from .topology import (
    RelaynaTopology,
    RoutedTasksSharedStatusShardedAggregationTopology,
    RoutedTasksSharedStatusTopology,
    ShardRoutingStrategy,
    SharedStatusWorkflowTopology,
    SharedTasksSharedStatusTopology,
    TaskIdRoutingStrategy,
    TaskTypeRoutingStrategy,
)


class RelaynaRabbitClient:
    """Reusable RabbitMQ client for task submission and status streaming."""

    def __init__(
        self,
        topology: RelaynaTopology,
        *,
        connection_name: str = "relayna-robust",
        alias_config: ContractAliasConfig | None = None,
    ) -> None:
        self._topology = topology
        self._connection_name = connection_name
        self._alias_config = alias_config
        self._connection: AbstractRobustConnection | None = None
        self._channel: AbstractRobustChannel | None = None
        self._tasks_exchange: AbstractRobustExchange | None = None
        self._workflow_exchange: AbstractRobustExchange | None = None
        self._status_exchange: AbstractRobustExchange | None = None
        self._lock = asyncio.Lock()
        self._initialized = False

    @property
    def topology(self) -> RelaynaTopology:
        return self._topology

    @property
    def alias_config(self) -> ContractAliasConfig | None:
        return self._alias_config

    async def initialize(self) -> None:
        async with self._lock:
            if self._initialized:
                return

            self._connection = await aio_pika.connect_robust(self._topology.connection_string(self._connection_name))
            self._channel = cast(AbstractRobustChannel, await self._connection.channel())
            await self._channel.set_qos(prefetch_count=self._topology.prefetch_count)
            self._tasks_exchange, self._status_exchange = await self._topology.declare_exchanges(self._channel)
            self._workflow_exchange = (
                self._tasks_exchange if self._topology.workflow_exchange_name() is not None else None
            )
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

    async def ensure_workflow_queue(self, stage: str) -> str:
        await self._ensure_ready()
        if self._channel is None or self._workflow_exchange is None:
            raise RuntimeError("Workflow exchange is not initialized")
        return await self._topology.ensure_workflow_queue(
            self._channel,
            workflow_exchange=self._workflow_exchange,
            stage=stage,
        )

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

    async def ensure_retry_infrastructure(
        self,
        *,
        source_queue_name: str,
        delay_ms: int,
        retry_queue_suffix: str = ".retry",
        dead_letter_queue_suffix: str = ".dlq",
    ) -> RetryInfrastructure:
        await self._ensure_ready()
        if self._channel is None:
            raise RuntimeError("RabbitMQ connection is not initialized")
        if not source_queue_name.strip():
            raise ValueError("source_queue_name must not be empty")
        if int(delay_ms) <= 0:
            raise ValueError("delay_ms must be greater than zero")

        retry_queue_name = f"{source_queue_name}{retry_queue_suffix}"
        dead_letter_queue_name = f"{source_queue_name}{dead_letter_queue_suffix}"

        await self._channel.declare_queue(
            retry_queue_name,
            durable=True,
            arguments={
                "x-message-ttl": int(delay_ms),
                "x-dead-letter-exchange": "",
                "x-dead-letter-routing-key": source_queue_name,
            },
        )
        await self._channel.declare_queue(dead_letter_queue_name, durable=True, arguments=None)
        return RetryInfrastructure(
            source_queue_name=source_queue_name,
            retry_queue_name=retry_queue_name,
            dead_letter_queue_name=dead_letter_queue_name,
        )

    async def publish_task(
        self,
        task: BaseModel | Mapping[str, Any],
        *,
        headers: Mapping[str, Any] | None = None,
    ) -> None:
        if isinstance(self._topology, SharedStatusWorkflowTopology):
            raise RuntimeError("SharedStatusWorkflowTopology requires publish_to_stage(...) or publish_to_entry(...)")
        await self._ensure_ready()
        if self._tasks_exchange is None:
            raise RuntimeError("Tasks exchange is not initialized")
        task_dict = self._prepare_task_payload(task)
        message_headers = {"task_id": str(task_dict.get("task_id", ""))}
        message_headers.update(dict(headers or {}))
        message = Message(
            _to_json_bytes(task_dict),
            content_type="application/json",
            delivery_mode=DeliveryMode.PERSISTENT,
            correlation_id=str(task_dict.get("correlation_id") or task_dict.get("task_id", "")) or None,
            priority=cast(int | None, task_dict.get("priority")),
            headers=cast(Any, message_headers),
        )
        _clear_default_priority(message, priority=cast(int | None, task_dict.get("priority")))
        await self._tasks_exchange.publish(message, routing_key=self._topology.task_routing_key(task_dict))

    async def publish_tasks(
        self,
        tasks: Sequence[BaseModel | Mapping[str, Any]],
        *,
        mode: Literal["individual", "batch_envelope"] = "individual",
        batch_id: str | None = None,
        meta: Mapping[str, Any] | None = None,
    ) -> None:
        prepared_tasks = [self._prepare_task_payload(task) for task in tasks]
        if mode == "individual":
            for task in prepared_tasks:
                await self.publish_task(task)
            return
        if mode != "batch_envelope":
            raise ValueError(f"Unsupported publish mode '{mode}'.")
        if not batch_id or not str(batch_id).strip():
            raise ValueError("batch_id is required when mode='batch_envelope'.")
        envelope = BatchTaskEnvelope(
            batch_id=str(batch_id),
            tasks=[TaskEnvelope.model_validate(task) for task in prepared_tasks],
            meta=dict(meta or {}),
        )
        await self._publish_batch_envelope(
            envelope.model_dump(mode="json", exclude_none=True),
            priority=self._resolve_batch_priority(prepared_tasks),
        )

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

    async def publish_workflow(
        self,
        payload: WorkflowEnvelope | Mapping[str, Any],
        *,
        headers: Mapping[str, Any] | None = None,
    ) -> None:
        workflow_payload = self._prepare_workflow_payload(payload)
        routing_key = self._topology.workflow_publish_routing_key(workflow_payload["stage"])
        await self.publish_workflow_message(workflow_payload, routing_key=routing_key, headers=headers)

    async def publish_to_stage(
        self,
        payload: WorkflowEnvelope | Mapping[str, Any],
        *,
        stage: str,
        headers: Mapping[str, Any] | None = None,
    ) -> None:
        workflow_payload = self._prepare_workflow_payload(payload, stage=stage)
        routing_key = self._topology.workflow_publish_routing_key(stage)
        await self.publish_workflow_message(workflow_payload, routing_key=routing_key, headers=headers)

    async def publish_to_entry(
        self,
        payload: WorkflowEnvelope | Mapping[str, Any],
        *,
        route: str,
        headers: Mapping[str, Any] | None = None,
    ) -> None:
        if not isinstance(self._topology, SharedStatusWorkflowTopology):
            raise RuntimeError("publish_to_entry(...) requires SharedStatusWorkflowTopology")
        stage = self._topology.workflow_entry_target_stage(route)
        workflow_payload = self._prepare_workflow_payload(payload, stage=stage)
        routing_key = self._topology.workflow_entry_routing_key(route)
        await self.publish_workflow_message(workflow_payload, routing_key=routing_key, headers=headers)

    async def publish_workflow_message(
        self,
        payload: WorkflowEnvelope | Mapping[str, Any],
        *,
        routing_key: str,
        headers: Mapping[str, Any] | None = None,
    ) -> None:
        await self._ensure_ready()
        if self._workflow_exchange is None:
            raise RuntimeError("Workflow exchange is not initialized")
        workflow_payload = self._prepare_workflow_payload(payload)
        message_headers = {
            "task_id": str(workflow_payload.get("task_id", "")),
            "message_id": str(workflow_payload.get("message_id", "")),
            "stage": str(workflow_payload.get("stage", "")),
        }
        origin_stage = workflow_payload.get("origin_stage")
        if origin_stage:
            message_headers["origin_stage"] = str(origin_stage)
        message_headers.update(dict(headers or {}))
        message = Message(
            _to_json_bytes(workflow_payload),
            content_type="application/json",
            delivery_mode=DeliveryMode.PERSISTENT,
            correlation_id=str(workflow_payload.get("correlation_id") or workflow_payload.get("task_id", "")) or None,
            priority=cast(int | None, workflow_payload.get("priority")),
            headers=cast(Any, message_headers),
        )
        _clear_default_priority(message, priority=cast(int | None, workflow_payload.get("priority")))
        await self._workflow_exchange.publish(message, routing_key=routing_key)

    async def publish_raw_to_queue(
        self,
        queue_name: str,
        body: bytes,
        *,
        correlation_id: str | None = None,
        headers: Mapping[str, Any] | None = None,
        content_type: str | None = "application/json",
        delivery_mode: DeliveryMode = DeliveryMode.PERSISTENT,
    ) -> None:
        await self._ensure_ready()
        if self._channel is None:
            raise RuntimeError("RabbitMQ connection is not initialized")
        message = Message(
            body,
            content_type=content_type,
            delivery_mode=delivery_mode,
            correlation_id=correlation_id,
            headers=dict(headers or {}),
        )
        await self._channel.default_exchange.publish(message, routing_key=queue_name)

    async def acquire_channel(self, prefetch: int = 200) -> AbstractChannel:
        await self._ensure_ready()
        if self._connection is None:
            raise RuntimeError("RabbitMQ robust connection is not available")
        channel = await self._connection.channel()
        await channel.set_qos(prefetch_count=prefetch)
        return channel

    async def inspect_queue(self, queue_name: str) -> QueueInspection | None:
        await self._ensure_ready()
        channel = None
        try:
            channel = await self.acquire_channel(prefetch=1)
            queue = await channel.declare_queue(queue_name, durable=True, passive=True)
            result = getattr(queue, "declaration_result", None)
            return QueueInspection(
                queue_name=queue_name,
                message_count=int(getattr(result, "message_count", 0)),
                consumer_count=int(getattr(result, "consumer_count", 0)),
            )
        except Exception:
            return None
        finally:
            if channel is not None:
                try:
                    await channel.close()
                except Exception:
                    pass

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
            self._workflow_exchange = None
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
            event_dict = normalize_contract_aliases(_to_dict(event), self._alias_config, drop_aliases=True)

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

    def _prepare_task_payload(self, task: BaseModel | Mapping[str, Any]) -> dict[str, Any]:
        normalized = normalize_contract_aliases(_to_dict(task), self._alias_config, drop_aliases=True)
        envelope = TaskEnvelope.model_validate(normalized)
        return envelope.model_dump(mode="json", exclude_none=True)

    def _prepare_workflow_payload(
        self,
        payload: WorkflowEnvelope | Mapping[str, Any],
        *,
        stage: str | None = None,
    ) -> dict[str, Any]:
        if isinstance(payload, WorkflowEnvelope):
            workflow_dict = payload.as_transport_dict()
        elif isinstance(payload, BaseModel):
            workflow_dict = _to_dict(payload)
        else:
            workflow_dict = normalize_contract_aliases(_to_dict(payload), self._alias_config, drop_aliases=True)
        if stage is not None:
            workflow_dict["stage"] = stage
        envelope = WorkflowEnvelope.model_validate(workflow_dict)
        return envelope.as_transport_dict()

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

    async def _publish_batch_envelope(self, payload: dict[str, Any], *, priority: int | None) -> None:
        await self._ensure_ready()
        if self._tasks_exchange is None:
            raise RuntimeError("Tasks exchange is not initialized")
        tasks = payload.get("tasks")
        first_task = tasks[0] if isinstance(tasks, list) and tasks else payload
        batch_id = str(payload.get("batch_id", ""))
        message = Message(
            _to_json_bytes(payload),
            content_type="application/json",
            delivery_mode=DeliveryMode.PERSISTENT,
            correlation_id=batch_id or None,
            priority=priority,
            headers={"batch_id": batch_id, "batch_size": len(tasks) if isinstance(tasks, list) else 0},
        )
        _clear_default_priority(message, priority=priority)
        await self._tasks_exchange.publish(message, routing_key=self._topology.task_routing_key(first_task))

    def _resolve_batch_priority(self, tasks: Sequence[Mapping[str, Any]]) -> int | None:
        priorities = {cast(int | None, task.get("priority")) for task in tasks}
        if len(priorities) > 1:
            raise ValueError("batch_envelope tasks must all share the same priority when priority is provided")
        return next(iter(priorities), None)


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


def _clear_default_priority(message: Message, *, priority: int | None) -> None:
    if priority is None:
        message.priority = None


@dataclass(slots=True)
class RetryInfrastructure:
    source_queue_name: str
    retry_queue_name: str
    dead_letter_queue_name: str


@dataclass(slots=True)
class QueueInspection:
    queue_name: str
    message_count: int
    consumer_count: int


__all__ = [
    "DirectQueuePublisher",
    "QueueInspection",
    "RelaynaRabbitClient",
    "RetryInfrastructure",
    "RoutedTasksSharedStatusShardedAggregationTopology",
    "RoutedTasksSharedStatusTopology",
    "ShardRoutingStrategy",
    "SharedStatusWorkflowTopology",
    "TaskTypeRoutingStrategy",
    "TaskIdRoutingStrategy",
    "declare_stream_queue",
]
