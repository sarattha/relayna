from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..topology import RelaynaTopology


def resolve_task_routing_key(topology: RelaynaTopology, task: Mapping[str, Any]) -> str:
    return topology.task_routing_key(task)


def resolve_status_routing_key(topology: RelaynaTopology, event: Mapping[str, Any]) -> str:
    return topology.status_routing_key(event)


def resolve_workflow_queue(topology: RelaynaTopology, stage: str) -> str:
    return topology.workflow_queue_name(stage)


__all__ = ["resolve_status_routing_key", "resolve_task_routing_key", "resolve_workflow_queue"]
