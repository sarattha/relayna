from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from aio_pika import ExchangeType
from aio_pika.abc import AbstractChannel, AbstractExchange, AbstractRobustChannel, AbstractRobustExchange
from pydantic import BaseModel

from ..contracts import normalize_contract_aliases
from .base import (
    merge_queue_arguments,
    raise_on_argument_conflicts,
    to_dict,
    validate_queue_max_priority,
    with_validated_queue_max_priority,
)


@dataclass(slots=True, frozen=True)
class WorkflowStage:
    name: str
    queue: str
    binding_keys: tuple[str, ...]
    publish_routing_key: str
    queue_arguments_overrides: dict[str, Any] = field(default_factory=dict)
    queue_kwargs: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class WorkflowEntryRoute:
    name: str
    routing_key: str
    target_stage: str


@dataclass(slots=True)
class SharedStatusWorkflowTopology:
    rabbitmq_url: str
    workflow_exchange: str
    status_exchange: str
    status_queue: str
    stages: tuple[WorkflowStage, ...]
    entry_routes: tuple[WorkflowEntryRoute, ...] = ()
    dead_letter_exchange: str | None = None
    prefetch_count: int = 1
    workflow_consumer_timeout_ms: int | None = None
    workflow_single_active_consumer: bool | None = None
    workflow_max_priority: int | None = None
    workflow_queue_type: str | None = None
    workflow_queue_arguments_overrides: dict[str, Any] = field(default_factory=dict)
    workflow_queue_kwargs: dict[str, Any] = field(default_factory=dict)
    status_use_streams: bool = True
    status_queue_ttl_ms: int | None = None
    status_stream_max_length_gb: int | None = None
    status_stream_max_segment_size_mb: int | None = None
    status_stream_initial_offset: str = "last"
    status_queue_arguments_overrides: dict[str, Any] = field(default_factory=dict)
    status_queue_kwargs: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        stage_names: set[str] = set()
        queue_names: set[str] = set()
        if not self.stages:
            raise ValueError("At least one workflow stage must be configured")
        for stage in self.stages:
            stage_name = stage.name.strip()
            if not stage_name:
                raise ValueError("Workflow stage names must not be empty")
            if stage_name in stage_names:
                raise ValueError(f"Duplicate workflow stage name '{stage_name}'")
            stage_names.add(stage_name)
            queue_name = stage.queue.strip()
            if not queue_name:
                raise ValueError(f"Workflow stage '{stage_name}' must define a queue name")
            if queue_name in queue_names:
                raise ValueError(f"Duplicate workflow queue name '{queue_name}'")
            queue_names.add(queue_name)
            binding_keys = tuple(binding_key.strip() for binding_key in stage.binding_keys if str(binding_key).strip())
            if not binding_keys:
                raise ValueError(f"Workflow stage '{stage_name}' must define at least one binding key")
            if not stage.publish_routing_key.strip():
                raise ValueError(f"Workflow stage '{stage_name}' must define publish_routing_key")

        route_names: set[str] = set()
        for route in self.entry_routes:
            route_name = route.name.strip()
            if not route_name:
                raise ValueError("Workflow entry route names must not be empty")
            if route_name in route_names:
                raise ValueError(f"Duplicate workflow entry route '{route_name}'")
            route_names.add(route_name)
            if not route.routing_key.strip():
                raise ValueError(f"Workflow entry route '{route_name}' must define routing_key")
            if route.target_stage not in stage_names:
                raise ValueError(
                    f"Workflow entry route '{route_name}' references unknown target stage '{route.target_stage}'"
                )

    def connection_string(self, connection_name: str | None = None) -> str:
        if connection_name:
            separator = "&" if "?" in self.rabbitmq_url else "?"
            return f"{self.rabbitmq_url}{separator}name={connection_name}"
        return self.rabbitmq_url

    def task_queue_arguments(self) -> dict[str, Any]:
        stage = self.default_workflow_stage()
        if stage is None:
            raise RuntimeError("Workflow topology does not define a default workflow stage")
        return self.workflow_queue_arguments(stage)

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
        return self.workflow_exchange

    def workflow_stage_names(self) -> tuple[str, ...]:
        return tuple(stage.name for stage in self.stages)

    def workflow_queue_names(self) -> tuple[str, ...]:
        return tuple(stage.queue for stage in self.stages)

    def workflow_queue_name(self, stage: str) -> str:
        return self._stage(stage).queue

    def workflow_binding_keys(self, stage: str) -> tuple[str, ...]:
        resolved = self._stage(stage)
        return tuple(binding_key.strip() for binding_key in resolved.binding_keys if binding_key.strip())

    def workflow_queue_arguments(self, stage: str) -> dict[str, Any]:
        resolved = self._stage(stage)
        builtins: dict[str, Any] = {}
        if self.dead_letter_exchange:
            builtins["x-dead-letter-exchange"] = self.dead_letter_exchange
        if self.workflow_consumer_timeout_ms is not None:
            builtins["x-consumer-timeout"] = int(self.workflow_consumer_timeout_ms)
        if self.workflow_single_active_consumer is not None:
            builtins["x-single-active-consumer"] = self.workflow_single_active_consumer
        if self.workflow_max_priority is not None:
            builtins["x-max-priority"] = validate_queue_max_priority(self.workflow_max_priority, label="workflow")
        if self.workflow_queue_type is not None:
            builtins["x-queue-type"] = self.workflow_queue_type

        global_overrides = dict(self.workflow_queue_arguments_overrides)
        stage_overrides = dict(resolved.queue_arguments_overrides)
        global_kwargs = dict(self.workflow_queue_kwargs)
        stage_kwargs = dict(resolved.queue_kwargs)

        raise_on_argument_conflicts(
            "workflow", "global overrides", global_overrides, "stage overrides", stage_overrides
        )
        raise_on_argument_conflicts("workflow", "global kwargs", global_kwargs, "stage kwargs", stage_kwargs)
        combined_overrides = global_overrides | stage_overrides
        combined_kwargs = global_kwargs | stage_kwargs
        return with_validated_queue_max_priority(
            merge_queue_arguments(
                "workflow",
                builtins=builtins,
                overrides=combined_overrides,
                kwargs=combined_kwargs,
            ),
            label="workflow",
        )

    def workflow_publish_routing_key(self, stage: str) -> str:
        return self._stage(stage).publish_routing_key.strip()

    def workflow_entry_routing_key(self, route: str) -> str:
        return self._entry_route(route).routing_key.strip()

    def workflow_entry_target_stage(self, route: str) -> str:
        return self._entry_route(route).target_stage

    def default_workflow_stage(self) -> str | None:
        return self.stages[0].name if self.stages else None

    def task_queue_name(self) -> str:
        stage = self.default_workflow_stage()
        if stage is None:
            raise RuntimeError("Workflow topology does not define a default workflow stage")
        return self.workflow_queue_name(stage)

    def status_queue_name(self) -> str:
        return self.status_queue

    def aggregation_queue_name(self, shards: Sequence[int], *, queue_name: str | None = None) -> str:
        del shards
        if queue_name is not None:
            return queue_name
        raise RuntimeError("Topology does not define shard-aware aggregation queues")

    def task_binding_keys(self) -> tuple[str, ...]:
        stage = self.default_workflow_stage()
        if stage is None:
            raise RuntimeError("Workflow topology does not define a default workflow stage")
        return self.workflow_binding_keys(stage)

    def status_binding_keys(self) -> tuple[str, ...]:
        return ("#",)

    def aggregation_binding_keys(self, shards: Sequence[int]) -> tuple[str, ...]:
        del shards
        raise RuntimeError("Topology does not define shard-aware aggregation queues")

    def task_routing_key(self, task: BaseModel | Mapping[str, Any]) -> str:
        del task
        raise RuntimeError("SharedStatusWorkflowTopology requires publish_to_stage(...) or publish_to_entry(...)")

    def status_routing_key(self, event: BaseModel | Mapping[str, Any]) -> str:
        data = to_dict(event)
        normalized = normalize_contract_aliases(data, drop_aliases=True)
        task_id = str(normalized.get("task_id", ""))
        if not task_id:
            raise ValueError("status event missing task_id")
        return task_id

    def aggregation_status_routing_key(self, event: BaseModel | Mapping[str, Any]) -> str:
        raise RuntimeError("Topology does not define shard-aware aggregation queues")

    def aggregation_shard(self, event: BaseModel | Mapping[str, Any]) -> int:
        raise RuntimeError("Topology does not define shard-aware aggregation queues")

    async def declare_exchanges(
        self,
        channel: AbstractRobustChannel,
    ) -> tuple[AbstractRobustExchange, AbstractRobustExchange]:
        if self.dead_letter_exchange:
            await channel.declare_exchange(self.dead_letter_exchange, ExchangeType.FANOUT, durable=True)
        workflow_exchange = await channel.declare_exchange(self.workflow_exchange, ExchangeType.TOPIC, durable=True)
        status_exchange = await channel.declare_exchange(self.status_exchange, ExchangeType.TOPIC, durable=True)
        return workflow_exchange, status_exchange

    async def declare_queues(
        self,
        channel: AbstractRobustChannel,
        *,
        tasks_exchange: AbstractRobustExchange,
        status_exchange: AbstractRobustExchange,
    ) -> None:
        await self.ensure_status_queue(channel, status_exchange=status_exchange)
        for stage in self.workflow_stage_names():
            await self.ensure_workflow_queue(channel, workflow_exchange=tasks_exchange, stage=stage)

    async def ensure_tasks_queue(
        self,
        channel: AbstractChannel,
        *,
        tasks_exchange: AbstractExchange,
    ) -> str:
        stage = self.default_workflow_stage()
        if stage is None:
            raise RuntimeError("Workflow topology does not define a default workflow stage")
        return await self.ensure_workflow_queue(channel, workflow_exchange=tasks_exchange, stage=stage)

    async def ensure_workflow_queue(
        self,
        channel: AbstractChannel,
        *,
        workflow_exchange: AbstractExchange,
        stage: str,
    ) -> str:
        queue_name = self.workflow_queue_name(stage)
        queue = await channel.declare_queue(
            queue_name,
            durable=True,
            arguments=self.workflow_queue_arguments(stage) or None,
        )
        for routing_key in self.workflow_binding_keys(stage):
            await queue.bind(workflow_exchange, routing_key=routing_key)
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

    def _stage(self, stage: str) -> WorkflowStage:
        for item in self.stages:
            if item.name == stage:
                return item
        raise KeyError(f"Unknown workflow stage '{stage}'")

    def _entry_route(self, route: str) -> WorkflowEntryRoute:
        for item in self.entry_routes:
            if item.name == route:
                return item
        raise KeyError(f"Unknown workflow entry route '{route}'")


__all__ = ["SharedStatusWorkflowTopology", "WorkflowEntryRoute", "WorkflowStage"]
