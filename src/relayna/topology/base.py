from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol, cast

from aio_pika.abc import AbstractChannel, AbstractExchange, AbstractRobustChannel, AbstractRobustExchange
from pydantic import BaseModel

from ..contracts import ContractAliasConfig, normalize_contract_aliases


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

    def workflow_exchange_name(self) -> str | None: ...

    def workflow_stage_names(self) -> tuple[str, ...]: ...

    def workflow_queue_names(self) -> tuple[str, ...]: ...

    def workflow_queue_name(self, stage: str) -> str: ...

    def workflow_binding_keys(self, stage: str) -> tuple[str, ...]: ...

    def workflow_queue_arguments(self, stage: str) -> dict[str, Any]: ...

    def workflow_publish_routing_key(self, stage: str) -> str: ...

    def workflow_entry_routing_key(self, route: str) -> str: ...

    def default_workflow_stage(self) -> str | None: ...

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

    async def ensure_workflow_queue(
        self,
        channel: AbstractChannel,
        *,
        workflow_exchange: AbstractExchange,
        stage: str,
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
        data = to_dict(event)
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
        data = to_dict(event)
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


class TaskTypeRoutingStrategy(TaskIdRoutingStrategy):
    """Routes tasks by task_type while keeping statuses keyed by task_id."""

    def __init__(self, *, alias_config: ContractAliasConfig | None = None) -> None:
        super().__init__("", alias_config=alias_config)

    def task_routing_key(self, task: BaseModel | Mapping[str, Any]) -> str:
        data = to_dict(task)
        normalized = normalize_contract_aliases(data, self._alias_config, drop_aliases=True)
        task_type = str(normalized.get("task_type") or "").strip()
        if not task_type:
            raise ValueError("task message missing task_type")
        return task_type


def aggregation_parent_task_id(event: dict[str, Any]) -> str:
    meta = event.get("meta")
    if not isinstance(meta, Mapping):
        return ""
    return str(meta.get("parent_task_id") or "")


def to_dict(payload: BaseModel | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(payload, BaseModel):
        return cast(dict[str, Any], payload.model_dump(mode="json", exclude_none=True))
    return dict(payload)


def validate_queue_max_priority(value: Any, *, label: str) -> int:
    priority = int(value)
    if priority < 1 or priority > 255:
        raise ValueError(f"{label}_max_priority must be between 1 and 255")
    return priority


def with_validated_queue_max_priority(arguments: dict[str, Any], *, label: str) -> dict[str, Any]:
    max_priority = arguments.get("x-max-priority")
    if max_priority is not None:
        arguments["x-max-priority"] = validate_queue_max_priority(max_priority, label=label)
    return arguments


def merge_queue_arguments(
    queue_family: str,
    *,
    builtins: Mapping[str, Any],
    overrides: Mapping[str, Any],
    kwargs: Mapping[str, Any],
) -> dict[str, Any]:
    builtin_args = dict(builtins)
    override_args = dict(overrides)
    kwarg_args = dict(kwargs)
    raise_on_argument_conflicts(queue_family, "built-in fields", builtin_args, "overrides", override_args)
    raise_on_argument_conflicts(queue_family, "built-in fields", builtin_args, "kwargs", kwarg_args)
    raise_on_argument_conflicts(queue_family, "overrides", override_args, "kwargs", kwarg_args)
    return builtin_args | override_args | kwarg_args


def raise_on_argument_conflicts(
    queue_family: str,
    left_name: str,
    left: Mapping[str, Any],
    right_name: str,
    right: Mapping[str, Any],
) -> None:
    duplicate_keys = sorted(set(left).intersection(right))
    if not duplicate_keys:
        return
    duplicates = ", ".join(duplicate_keys)
    raise ValueError(
        f"Duplicate {queue_family} queue arguments configured in {left_name} and {right_name}: {duplicates}"
    )


__all__ = [
    "RelaynaTopology",
    "RoutingStrategy",
    "ShardRoutingStrategy",
    "TaskIdRoutingStrategy",
    "TaskTypeRoutingStrategy",
    "aggregation_parent_task_id",
    "merge_queue_arguments",
    "raise_on_argument_conflicts",
    "to_dict",
    "validate_queue_max_priority",
    "with_validated_queue_max_priority",
]
