from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aio_pika import ExchangeType
from aio_pika.abc import AbstractChannel, AbstractExchange, AbstractRobustChannel, AbstractRobustExchange
from pydantic import BaseModel

from .base import (
    RoutingStrategy,
    TaskIdRoutingStrategy,
    TaskTypeRoutingStrategy,
    merge_queue_arguments,
    validate_queue_max_priority,
    with_validated_queue_max_priority,
)


@dataclass(slots=True)
class SharedTasksSharedStatusTopology:
    rabbitmq_url: str
    tasks_exchange: str
    tasks_queue: str
    tasks_routing_key: str
    status_exchange: str
    status_queue: str
    dead_letter_exchange: str | None = None
    prefetch_count: int = 1
    tasks_message_ttl_ms: int | None = None
    task_consumer_timeout_ms: int | None = None
    task_single_active_consumer: bool | None = None
    task_max_priority: int | None = None
    task_queue_type: str | None = None
    task_queue_arguments_overrides: dict[str, Any] = field(default_factory=dict)
    task_queue_kwargs: dict[str, Any] = field(default_factory=dict)
    status_use_streams: bool = True
    status_queue_ttl_ms: int | None = None
    status_stream_max_length_gb: int | None = None
    status_stream_max_segment_size_mb: int | None = None
    status_stream_initial_offset: str = "last"
    status_queue_arguments_overrides: dict[str, Any] = field(default_factory=dict)
    status_queue_kwargs: dict[str, Any] = field(default_factory=dict)
    routing_strategy: RoutingStrategy | None = None

    def connection_string(self, connection_name: str | None = None) -> str:
        if connection_name:
            separator = "&" if "?" in self.rabbitmq_url else "?"
            return f"{self.rabbitmq_url}{separator}name={connection_name}"
        return self.rabbitmq_url

    def task_queue_arguments(self) -> dict[str, Any]:
        builtins: dict[str, Any] = {}
        if self.tasks_message_ttl_ms:
            builtins["x-message-ttl"] = int(self.tasks_message_ttl_ms)
        if self.dead_letter_exchange:
            builtins["x-dead-letter-exchange"] = self.dead_letter_exchange
        if self.task_consumer_timeout_ms is not None:
            builtins["x-consumer-timeout"] = int(self.task_consumer_timeout_ms)
        if self.task_single_active_consumer is not None:
            builtins["x-single-active-consumer"] = self.task_single_active_consumer
        if self.task_max_priority is not None:
            builtins["x-max-priority"] = validate_queue_max_priority(self.task_max_priority, label="task")
        if self.task_queue_type is not None:
            builtins["x-queue-type"] = self.task_queue_type
        return with_validated_queue_max_priority(
            merge_queue_arguments(
                "task",
                builtins=builtins,
                overrides=self.task_queue_arguments_overrides,
                kwargs=self.task_queue_kwargs,
            ),
            label="task",
        )

    def status_queue_arguments(self) -> dict[str, Any]:
        builtins: dict[str, Any]
        if self.status_use_streams:
            builtins = {"x-queue-type": "stream"}
            if self.status_stream_max_length_gb is not None:
                builtins["x-max-length-bytes"] = int(self.status_stream_max_length_gb) * 1024**3
            if self.status_stream_max_segment_size_mb is not None:
                builtins["x-stream-max-segment-size-bytes"] = int(self.status_stream_max_segment_size_mb) * 1024**2
        else:
            builtins = {}
            if self.status_queue_ttl_ms:
                builtins["x-expires"] = int(self.status_queue_ttl_ms)
        return merge_queue_arguments(
            "status",
            builtins=builtins,
            overrides=self.status_queue_arguments_overrides,
            kwargs=self.status_queue_kwargs,
        )

    def status_stream_consume_arguments(self) -> dict[str, Any]:
        if not self.status_use_streams:
            return {}
        return {"x-stream-offset": self.status_stream_initial_offset}

    def aggregation_queue_arguments(self) -> dict[str, Any]:
        return {}

    def workflow_exchange_name(self) -> str | None:
        return self.tasks_exchange

    def workflow_stage_names(self) -> tuple[str, ...]:
        return ("default",)

    def workflow_queue_names(self) -> tuple[str, ...]:
        return (self.tasks_queue,)

    def workflow_queue_name(self, stage: str) -> str:
        self._require_default_workflow_stage(stage)
        return self.task_queue_name()

    def workflow_binding_keys(self, stage: str) -> tuple[str, ...]:
        self._require_default_workflow_stage(stage)
        return self.task_binding_keys()

    def workflow_queue_arguments(self, stage: str) -> dict[str, Any]:
        self._require_default_workflow_stage(stage)
        return self.task_queue_arguments()

    def workflow_publish_routing_key(self, stage: str) -> str:
        self._require_default_workflow_stage(stage)
        routing_key = str(self.tasks_routing_key).strip()
        if not routing_key:
            raise RuntimeError("Topology does not define a single workflow publish routing key")
        return routing_key

    def workflow_entry_routing_key(self, route: str) -> str:
        self._require_default_workflow_stage(route)
        return self.workflow_publish_routing_key(route)

    def default_workflow_stage(self) -> str | None:
        return "default"

    def task_queue_name(self) -> str:
        return self.tasks_queue

    def status_queue_name(self) -> str:
        return self.status_queue

    def aggregation_queue_name(self, shards, *, queue_name: str | None = None) -> str:
        del shards
        if queue_name is not None:
            return queue_name
        raise RuntimeError("Topology does not define shard-aware aggregation queues")

    def task_binding_keys(self) -> tuple[str, ...]:
        return (self.tasks_routing_key,)

    def status_binding_keys(self) -> tuple[str, ...]:
        return ("#",)

    def aggregation_binding_keys(self, shards) -> tuple[str, ...]:
        del shards
        raise RuntimeError("Topology does not define shard-aware aggregation queues")

    def task_routing_key(self, task: BaseModel | dict[str, Any]) -> str:
        return self._routing().task_routing_key(task)

    def status_routing_key(self, event: BaseModel | dict[str, Any]) -> str:
        return self._routing().status_routing_key(event)

    def aggregation_status_routing_key(self, event: BaseModel | dict[str, Any]) -> str:
        raise RuntimeError("Topology does not define shard-aware aggregation queues")

    def aggregation_shard(self, event: BaseModel | dict[str, Any]) -> int:
        raise RuntimeError("Topology does not define shard-aware aggregation queues")

    async def declare_exchanges(
        self,
        channel: AbstractRobustChannel,
    ) -> tuple[AbstractRobustExchange, AbstractRobustExchange]:
        if self.dead_letter_exchange:
            await channel.declare_exchange(self.dead_letter_exchange, ExchangeType.FANOUT, durable=True)
        tasks_exchange = await channel.declare_exchange(self.tasks_exchange, ExchangeType.DIRECT, durable=True)
        status_exchange = await channel.declare_exchange(self.status_exchange, ExchangeType.TOPIC, durable=True)
        return tasks_exchange, status_exchange

    async def declare_queues(
        self,
        channel: AbstractRobustChannel,
        *,
        tasks_exchange: AbstractRobustExchange,
        status_exchange: AbstractRobustExchange,
    ) -> None:
        await self.ensure_status_queue(channel, status_exchange=status_exchange)
        await self.ensure_tasks_queue(channel, tasks_exchange=tasks_exchange)

    async def ensure_tasks_queue(
        self,
        channel: AbstractChannel,
        *,
        tasks_exchange: AbstractExchange,
    ) -> str:
        queue = await channel.declare_queue(
            self.task_queue_name(),
            durable=True,
            arguments=self.task_queue_arguments() or None,
        )
        for routing_key in self.task_binding_keys():
            await queue.bind(tasks_exchange, routing_key=routing_key)
        return queue.name

    async def ensure_workflow_queue(
        self,
        channel: AbstractChannel,
        *,
        workflow_exchange: AbstractExchange,
        stage: str,
    ) -> str:
        self._require_default_workflow_stage(stage)
        return await self.ensure_tasks_queue(channel, tasks_exchange=workflow_exchange)

    async def ensure_status_queue(
        self,
        channel: AbstractChannel,
        *,
        status_exchange: AbstractExchange,
    ) -> str:
        queue = await channel.declare_queue(
            self.status_queue_name(),
            durable=True,
            arguments=self.status_queue_arguments() or None,
        )
        for routing_key in self.status_binding_keys():
            await queue.bind(status_exchange, routing_key=routing_key)
        return queue.name

    async def ensure_aggregation_queue(
        self,
        channel: AbstractChannel,
        *,
        status_exchange: AbstractExchange,
        shards,
        queue_name: str | None = None,
    ) -> str:
        del channel, status_exchange, shards, queue_name
        raise RuntimeError("Topology does not define shard-aware aggregation queues")

    def _routing(self) -> RoutingStrategy:
        return self.routing_strategy or TaskIdRoutingStrategy(self.tasks_routing_key)

    def _require_default_workflow_stage(self, stage: str) -> None:
        if stage != "default":
            raise KeyError(f"Unknown workflow stage '{stage}'")


@dataclass(slots=True)
class RoutedTasksSharedStatusTopology(SharedTasksSharedStatusTopology):
    task_types: tuple[str, ...] = field(default_factory=tuple)
    tasks_routing_key: str = field(init=False, default="", repr=False)

    def task_binding_keys(self) -> tuple[str, ...]:
        return self._normalized_task_types()

    def _routing(self) -> RoutingStrategy:
        return TaskTypeRoutingStrategy()

    def workflow_publish_routing_key(self, stage: str) -> str:
        self._require_default_workflow_stage(stage)
        raise RuntimeError("Routed task topologies do not define a single workflow publish routing key")

    def _normalized_task_types(self) -> tuple[str, ...]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in self.task_types:
            task_type = str(value).strip()
            if not task_type or task_type in seen:
                continue
            normalized.append(task_type)
            seen.add(task_type)
        if not normalized:
            raise ValueError("At least one task_type must be configured")
        return tuple(normalized)


__all__ = ["RoutedTasksSharedStatusTopology", "SharedTasksSharedStatusTopology"]
