from __future__ import annotations

from .base import RelaynaTopology
from .sharded_aggregation import (
    RoutedTasksSharedStatusShardedAggregationTopology,
    SharedTasksSharedStatusShardedAggregationTopology,
)
from .shared_tasks import RoutedTasksSharedStatusTopology, SharedTasksSharedStatusTopology
from .workflow import SharedStatusWorkflowTopology


def topology_kind(topology: RelaynaTopology) -> str:
    if isinstance(topology, SharedStatusWorkflowTopology):
        return "shared_status_workflow"
    if isinstance(topology, RoutedTasksSharedStatusShardedAggregationTopology):
        return "routed_tasks_shared_status_sharded_aggregation"
    if isinstance(topology, SharedTasksSharedStatusShardedAggregationTopology):
        return "shared_tasks_shared_status_sharded_aggregation"
    if isinstance(topology, RoutedTasksSharedStatusTopology):
        return "routed_tasks_shared_status"
    if isinstance(topology, SharedTasksSharedStatusTopology):
        return "shared_tasks_shared_status"
    return type(topology).__name__


__all__ = ["topology_kind"]
