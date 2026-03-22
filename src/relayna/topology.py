from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

from aio_pika import ExchangeType
from aio_pika.abc import (
    AbstractChannel,
    AbstractExchange,
    AbstractQueue,
    AbstractRobustChannel,
    AbstractRobustExchange,
)
from pydantic import BaseModel

from .contracts import ContractAliasConfig, normalize_contract_aliases


class RoutingStrategy(Protocol):
    def task_routing_key(self, task: BaseModel | Mapping[str, Any]) -> str: ...

    def status_routing_key(self, event: BaseModel | Mapping[str, Any]) -> str: ...


class RelaynaTopology(Protocol):
    rabbitmq_url: str
    prefetch_count: int

    def connection_string(self, connection_name: str | None = None) -> str: ...

    def task_queue_arguments(self) -> dict[str, Any]: ...

    def status_queue_arguments(self) -> dict[str, Any]: ...

    def status_stream_consume_arguments(self) -> dict[str, Any]: ...

    def aggregation_queue_arguments(self) -> dict[str, Any]: ...

    def task_queue_name(self) -> str: ...

    def status_queue_name(self) -> str: ...

    def aggregation_queue_name(self, shards: Sequence[int], *, queue_name: str | None = None) -> str: ...

    def task_binding_keys(self) -> tuple[str, ...]: ...

    def status_binding_keys(self) -> tuple[str, ...]: ...

    def aggregation_binding_keys(self, shards: Sequence[int]) -> tuple[str, ...]: ...

    def task_routing_key(self, task: BaseModel | Mapping[str, Any]) -> str: ...

    def status_routing_key(self, event: BaseModel | Mapping[str, Any]) -> str: ...

    def aggregation_status_routing_key(self, event: BaseModel | Mapping[str, Any]) -> str: ...

    def aggregation_shard(self, event: BaseModel | Mapping[str, Any]) -> int: ...

    async def declare_exchanges(
        self,
        channel: AbstractRobustChannel,
    ) -> tuple[AbstractRobustExchange, AbstractRobustExchange]: ...

    async def declare_queues(
        self,
        channel: AbstractRobustChannel,
        *,
        tasks_exchange: AbstractRobustExchange,
        status_exchange: AbstractRobustExchange,
    ) -> None: ...

    async def ensure_tasks_queue(
        self,
        channel: AbstractChannel,
        *,
        tasks_exchange: AbstractExchange,
    ) -> str: ...

    async def ensure_status_queue(
        self,
        channel: AbstractChannel,
        *,
        status_exchange: AbstractExchange,
    ) -> str: ...

    async def ensure_aggregation_queue(
        self,
        channel: AbstractChannel,
        *,
        status_exchange: AbstractExchange,
        shards: Sequence[int],
        queue_name: str | None = None,
    ) -> str: ...


class TaskIdRoutingStrategy:
    """Default route strategy: tasks to static key, statuses keyed by task_id."""

    def __init__(self, tasks_routing_key: str, *, alias_config: ContractAliasConfig | None = None) -> None:
        self._tasks_routing_key = tasks_routing_key
        self._alias_config = alias_config

    def task_routing_key(self, task: BaseModel | Mapping[str, Any]) -> str:
        return self._tasks_routing_key

    def status_routing_key(self, event: BaseModel | Mapping[str, Any]) -> str:
        data = _to_dict(event)
        normalized = normalize_contract_aliases(data, self._alias_config, drop_aliases=True)
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
        alias_config: ContractAliasConfig | None = None,
    ) -> None:
        super().__init__(tasks_routing_key, alias_config=alias_config)
        self._shard_count = max(1, int(shard_count))
        self._routing_prefix = routing_prefix
        self._key_extractor = key_extractor

    @property
    def shard_count(self) -> int:
        return self._shard_count

    @property
    def routing_prefix(self) -> str:
        return self._routing_prefix

    def shard_for_event(self, event: BaseModel | Mapping[str, Any]) -> int:
        data = _to_dict(event)
        normalized = normalize_contract_aliases(data, self._alias_config, drop_aliases=True)
        if self._key_extractor is not None:
            key = self._key_extractor(normalized)
        else:
            meta = normalized.get("meta")
            parent_task_id = meta.get("parent_task_id") if isinstance(meta, Mapping) else None
            key = str(parent_task_id or normalized.get("task_id") or "")
        if not key:
            raise ValueError("status event missing shard key")
        return int(hashlib.sha256(key.encode("utf-8")).hexdigest(), 16) % self._shard_count

    def status_routing_key(self, event: BaseModel | Mapping[str, Any]) -> str:
        return f"{self._routing_prefix}.{self.shard_for_event(event)}"


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
    status_use_streams: bool = True
    status_queue_ttl_ms: int | None = None
    status_stream_max_length_gb: int | None = None
    status_stream_max_segment_size_mb: int | None = None
    status_stream_initial_offset: str = "last"
    routing_strategy: RoutingStrategy | None = None

    def connection_string(self, connection_name: str | None = None) -> str:
        if connection_name:
            separator = "&" if "?" in self.rabbitmq_url else "?"
            return f"{self.rabbitmq_url}{separator}name={connection_name}"
        return self.rabbitmq_url

    def task_queue_arguments(self) -> dict[str, Any]:
        args: dict[str, Any] = {}
        if self.tasks_message_ttl_ms:
            args["x-message-ttl"] = int(self.tasks_message_ttl_ms)
        if self.dead_letter_exchange:
            args["x-dead-letter-exchange"] = self.dead_letter_exchange
        return args

    def status_queue_arguments(self) -> dict[str, Any]:
        if self.status_use_streams:
            args: dict[str, Any] = {"x-queue-type": "stream"}
            if self.status_stream_max_length_gb is not None:
                args["x-max-length-bytes"] = int(self.status_stream_max_length_gb) * 1024**3
            if self.status_stream_max_segment_size_mb is not None:
                args["x-stream-max-segment-size-bytes"] = int(self.status_stream_max_segment_size_mb) * 1024**2
            return args
        args: dict[str, Any] = {}
        if self.status_queue_ttl_ms:
            args["x-expires"] = int(self.status_queue_ttl_ms)
        return args

    def status_stream_consume_arguments(self) -> dict[str, Any]:
        if not self.status_use_streams:
            return {}
        return {"x-stream-offset": self.status_stream_initial_offset}

    def aggregation_queue_arguments(self) -> dict[str, Any]:
        return {}

    def task_queue_name(self) -> str:
        return self.tasks_queue

    def status_queue_name(self) -> str:
        return self.status_queue

    def aggregation_queue_name(self, shards: Sequence[int], *, queue_name: str | None = None) -> str:
        del shards
        if queue_name is not None:
            return queue_name
        raise RuntimeError("Topology does not define shard-aware aggregation queues")

    def task_binding_keys(self) -> tuple[str, ...]:
        return (self.tasks_routing_key,)

    def status_binding_keys(self) -> tuple[str, ...]:
        return ("#",)

    def aggregation_binding_keys(self, shards: Sequence[int]) -> tuple[str, ...]:
        del shards
        raise RuntimeError("Topology does not define shard-aware aggregation queues")

    def task_routing_key(self, task: BaseModel | Mapping[str, Any]) -> str:
        return self._routing().task_routing_key(task)

    def status_routing_key(self, event: BaseModel | Mapping[str, Any]) -> str:
        return self._routing().status_routing_key(event)

    def aggregation_status_routing_key(self, event: BaseModel | Mapping[str, Any]) -> str:
        raise RuntimeError("Topology does not define shard-aware aggregation queues")

    def aggregation_shard(self, event: BaseModel | Mapping[str, Any]) -> int:
        raise RuntimeError("Topology does not define shard-aware aggregation queues")

    async def declare_exchanges(
        self,
        channel: AbstractRobustChannel,
    ) -> tuple[AbstractRobustExchange, AbstractRobustExchange]:
        if self.dead_letter_exchange:
            await channel.declare_exchange(
                self.dead_letter_exchange,
                ExchangeType.FANOUT,
                durable=True,
            )
        tasks_exchange = await channel.declare_exchange(
            self.tasks_exchange,
            ExchangeType.DIRECT,
            durable=True,
        )
        status_exchange = await channel.declare_exchange(
            self.status_exchange,
            ExchangeType.TOPIC,
            durable=True,
        )
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
        shards: Sequence[int],
        queue_name: str | None = None,
    ) -> str:
        del channel, status_exchange, shards, queue_name
        raise RuntimeError("Topology does not define shard-aware aggregation queues")

    def _routing(self) -> RoutingStrategy:
        return self.routing_strategy or TaskIdRoutingStrategy(self.tasks_routing_key)


@dataclass(slots=True)
class SharedTasksSharedStatusShardedAggregationTopology(SharedTasksSharedStatusTopology):
    shard_count: int = 1
    aggregation_routing_prefix: str = "agg"
    aggregation_queue_template: str = "aggregation.queue.{shard}"
    aggregation_queue_name_prefix: str = "aggregation.queue.shards"
    aggregation_queue_arguments_overrides: dict[str, Any] = field(default_factory=dict)

    def aggregation_queue_arguments(self) -> dict[str, Any]:
        return dict(self.aggregation_queue_arguments_overrides)

    def aggregation_queue_name(self, shards: Sequence[int], *, queue_name: str | None = None) -> str:
        normalized_shards = self._normalize_shards(shards)
        return queue_name or self._aggregation_queue_name(normalized_shards)

    def aggregation_binding_keys(self, shards: Sequence[int]) -> tuple[str, ...]:
        normalized_shards = self._normalize_shards(shards)
        prefix = self._aggregation_routing().routing_prefix
        return tuple(f"{prefix}.{shard}" for shard in normalized_shards)

    def aggregation_status_routing_key(self, event: BaseModel | Mapping[str, Any]) -> str:
        return self._aggregation_routing().status_routing_key(event)

    def aggregation_shard(self, event: BaseModel | Mapping[str, Any]) -> int:
        return self._aggregation_routing().shard_for_event(event)

    async def declare_queues(
        self,
        channel: AbstractRobustChannel,
        *,
        tasks_exchange: AbstractRobustExchange,
        status_exchange: AbstractRobustExchange,
    ) -> None:
        # Python 3.13 can raise ``TypeError: super(type, obj)`` for zero-argument
        # ``super()`` inside ``@dataclass(slots=True)`` subclasses. Using the
        # explicit two-argument form keeps normal MRO behavior while avoiding that
        # runtime failure.
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
        shards: Sequence[int],
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

    def _normalize_shards(self, shards: Sequence[int]) -> tuple[int, ...]:
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
            key_extractor=_aggregation_parent_task_id,
        )


def _aggregation_parent_task_id(event: dict[str, Any]) -> str:
    meta = event.get("meta")
    if not isinstance(meta, Mapping):
        return ""
    return str(meta.get("parent_task_id") or "")


def _to_dict(payload: BaseModel | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(payload, BaseModel):
        return cast(dict[str, Any], payload.model_dump(mode="json", exclude_none=True))
    return dict(payload)
