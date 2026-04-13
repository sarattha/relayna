from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aio_pika.abc import AbstractChannel, AbstractExchange, AbstractRobustChannel, AbstractRobustExchange
from pydantic import BaseModel

from .base import (
    ShardRoutingStrategy,
    aggregation_parent_task_id,
    merge_queue_arguments,
    validate_queue_max_priority,
    with_validated_queue_max_priority,
)
from .shared_tasks import RoutedTasksSharedStatusTopology, SharedTasksSharedStatusTopology


@dataclass(slots=True)
class SharedTasksSharedStatusShardedAggregationTopology(SharedTasksSharedStatusTopology):
    shard_count: int = 1
    aggregation_routing_prefix: str = "agg"
    aggregation_queue_template: str = "aggregation.queue.{shard}"
    aggregation_queue_name_prefix: str = "aggregation.queue.shards"
    aggregation_consumer_timeout_ms: int | None = None
    aggregation_single_active_consumer: bool | None = None
    aggregation_max_priority: int | None = None
    aggregation_queue_type: str | None = None
    aggregation_queue_arguments_overrides: dict[str, Any] = field(default_factory=dict)
    aggregation_queue_kwargs: dict[str, Any] = field(default_factory=dict)

    def aggregation_queue_arguments(self) -> dict[str, Any]:
        builtins: dict[str, Any] = {}
        if self.aggregation_consumer_timeout_ms is not None:
            builtins["x-consumer-timeout"] = int(self.aggregation_consumer_timeout_ms)
        if self.aggregation_single_active_consumer is not None:
            builtins["x-single-active-consumer"] = self.aggregation_single_active_consumer
        if self.aggregation_max_priority is not None:
            builtins["x-max-priority"] = validate_queue_max_priority(self.aggregation_max_priority, label="aggregation")
        if self.aggregation_queue_type is not None:
            builtins["x-queue-type"] = self.aggregation_queue_type
        return with_validated_queue_max_priority(
            merge_queue_arguments(
                "aggregation",
                builtins=builtins,
                overrides=self.aggregation_queue_arguments_overrides,
                kwargs=self.aggregation_queue_kwargs,
            ),
            label="aggregation",
        )

    def aggregation_queue_name(self, shards, *, queue_name: str | None = None) -> str:
        normalized_shards = self._normalize_shards(shards)
        return queue_name or self._aggregation_queue_name(normalized_shards)

    def aggregation_binding_keys(self, shards) -> tuple[str, ...]:
        normalized_shards = self._normalize_shards(shards)
        prefix = self._aggregation_routing().routing_prefix
        return tuple(f"{prefix}.{shard}" for shard in normalized_shards)

    def aggregation_status_routing_key(self, event: BaseModel | dict[str, Any]) -> str:
        return self._aggregation_routing().status_routing_key(event)

    def aggregation_shard(self, event: BaseModel | dict[str, Any]) -> int:
        return self._aggregation_routing().shard_for_event(event)

    async def declare_queues(
        self,
        channel: AbstractRobustChannel,
        *,
        tasks_exchange: AbstractRobustExchange,
        status_exchange: AbstractRobustExchange,
    ) -> None:
        await super(SharedTasksSharedStatusShardedAggregationTopology, self).declare_queues(
            channel=channel,
            tasks_exchange=tasks_exchange,
            status_exchange=status_exchange,
        )

    async def ensure_aggregation_queue(
        self,
        channel: AbstractChannel,
        *,
        status_exchange: AbstractExchange,
        shards,
        queue_name: str | None = None,
    ) -> str:
        normalized_shards = self._normalize_shards(shards)
        resolved_queue_name = self.aggregation_queue_name(normalized_shards, queue_name=queue_name)
        queue = await channel.declare_queue(
            resolved_queue_name,
            durable=True,
            arguments=self.aggregation_queue_arguments() or None,
        )
        for routing_key in self.aggregation_binding_keys(normalized_shards):
            await queue.bind(status_exchange, routing_key=routing_key)
        return queue.name

    def _aggregation_queue_name(self, shards: tuple[int, ...]) -> str:
        if len(shards) == 1:
            return self.aggregation_queue_template.format(shard=shards[0])
        suffix = "-".join(str(shard) for shard in shards)
        return f"{self.aggregation_queue_name_prefix}.{suffix}"

    def _normalize_shards(self, shards) -> tuple[int, ...]:
        if not shards:
            raise ValueError("At least one aggregation shard must be selected")
        normalized = tuple(sorted({int(shard) for shard in shards}))
        shard_count = self._aggregation_routing().shard_count
        for shard in normalized:
            if shard < 0 or shard >= shard_count:
                raise ValueError(f"Aggregation shard {shard} is outside the configured range 0..{shard_count - 1}")
        return normalized

    def _aggregation_routing(self) -> ShardRoutingStrategy:
        return ShardRoutingStrategy(
            self.tasks_routing_key,
            shard_count=self.shard_count,
            routing_prefix=self.aggregation_routing_prefix,
            key_extractor=aggregation_parent_task_id,
        )


@dataclass(slots=True)
class RoutedTasksSharedStatusShardedAggregationTopology(RoutedTasksSharedStatusTopology):
    shard_count: int = 1
    aggregation_routing_prefix: str = "agg"
    aggregation_queue_template: str = "aggregation.queue.{shard}"
    aggregation_queue_name_prefix: str = "aggregation.queue.shards"
    aggregation_consumer_timeout_ms: int | None = None
    aggregation_single_active_consumer: bool | None = None
    aggregation_max_priority: int | None = None
    aggregation_queue_type: str | None = None
    aggregation_queue_arguments_overrides: dict[str, Any] = field(default_factory=dict)
    aggregation_queue_kwargs: dict[str, Any] = field(default_factory=dict)

    def aggregation_queue_arguments(self) -> dict[str, Any]:
        builtins: dict[str, Any] = {}
        if self.aggregation_consumer_timeout_ms is not None:
            builtins["x-consumer-timeout"] = int(self.aggregation_consumer_timeout_ms)
        if self.aggregation_single_active_consumer is not None:
            builtins["x-single-active-consumer"] = self.aggregation_single_active_consumer
        if self.aggregation_max_priority is not None:
            builtins["x-max-priority"] = validate_queue_max_priority(self.aggregation_max_priority, label="aggregation")
        if self.aggregation_queue_type is not None:
            builtins["x-queue-type"] = self.aggregation_queue_type
        return with_validated_queue_max_priority(
            merge_queue_arguments(
                "aggregation",
                builtins=builtins,
                overrides=self.aggregation_queue_arguments_overrides,
                kwargs=self.aggregation_queue_kwargs,
            ),
            label="aggregation",
        )

    def aggregation_queue_name(self, shards, *, queue_name: str | None = None) -> str:
        normalized_shards = self._normalize_shards(shards)
        return queue_name or self._aggregation_queue_name(normalized_shards)

    def aggregation_binding_keys(self, shards) -> tuple[str, ...]:
        normalized_shards = self._normalize_shards(shards)
        prefix = self._aggregation_routing().routing_prefix
        return tuple(f"{prefix}.{shard}" for shard in normalized_shards)

    def aggregation_status_routing_key(self, event: BaseModel | dict[str, Any]) -> str:
        return self._aggregation_routing().status_routing_key(event)

    def aggregation_shard(self, event: BaseModel | dict[str, Any]) -> int:
        return self._aggregation_routing().shard_for_event(event)

    async def declare_queues(
        self,
        channel: AbstractRobustChannel,
        *,
        tasks_exchange: AbstractRobustExchange,
        status_exchange: AbstractRobustExchange,
    ) -> None:
        await super(RoutedTasksSharedStatusShardedAggregationTopology, self).declare_queues(
            channel=channel,
            tasks_exchange=tasks_exchange,
            status_exchange=status_exchange,
        )

    async def ensure_aggregation_queue(
        self,
        channel: AbstractChannel,
        *,
        status_exchange: AbstractExchange,
        shards,
        queue_name: str | None = None,
    ) -> str:
        normalized_shards = self._normalize_shards(shards)
        resolved_queue_name = self.aggregation_queue_name(normalized_shards, queue_name=queue_name)
        queue = await channel.declare_queue(
            resolved_queue_name,
            durable=True,
            arguments=self.aggregation_queue_arguments() or None,
        )
        for routing_key in self.aggregation_binding_keys(normalized_shards):
            await queue.bind(status_exchange, routing_key=routing_key)
        return queue.name

    def _aggregation_queue_name(self, shards: tuple[int, ...]) -> str:
        if len(shards) == 1:
            return self.aggregation_queue_template.format(shard=shards[0])
        suffix = "-".join(str(shard) for shard in shards)
        return f"{self.aggregation_queue_name_prefix}.{suffix}"

    def _normalize_shards(self, shards) -> tuple[int, ...]:
        if not shards:
            raise ValueError("At least one aggregation shard must be selected")
        normalized = tuple(sorted({int(shard) for shard in shards}))
        shard_count = self._aggregation_routing().shard_count
        for shard in normalized:
            if shard < 0 or shard >= shard_count:
                raise ValueError(f"Aggregation shard {shard} is outside the configured range 0..{shard_count - 1}")
        return normalized

    def _aggregation_routing(self) -> ShardRoutingStrategy:
        return ShardRoutingStrategy(
            self._normalized_task_types()[0],
            shard_count=self.shard_count,
            routing_prefix=self.aggregation_routing_prefix,
            key_extractor=aggregation_parent_task_id,
        )


__all__ = [
    "RoutedTasksSharedStatusShardedAggregationTopology",
    "SharedTasksSharedStatusShardedAggregationTopology",
]
