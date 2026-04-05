from __future__ import annotations

from typing import Any

from .workflow import SharedStatusWorkflowTopology


def validate_topology(topology: Any) -> list[str]:
    """Returns validation errors discovered through the public topology surface."""

    errors: list[str] = []
    if not hasattr(topology, "connection_string"):
        errors.append("topology must expose connection_string(...)")
    if isinstance(topology, SharedStatusWorkflowTopology):
        if not topology.workflow_stage_names():
            errors.append("workflow topology must contain at least one stage")
        for stage in topology.workflow_stage_names():
            if not topology.workflow_queue_name(stage).strip():
                errors.append(f"workflow stage '{stage}' must define a queue name")
            if not topology.workflow_binding_keys(stage):
                errors.append(f"workflow stage '{stage}' must define at least one binding key")
    return errors


def summarize_topology(topology: Any) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "kind": type(topology).__name__,
        "prefetch_count": getattr(topology, "prefetch_count", None),
        "status_queue": topology.status_queue_name(),
    }
    if topology.workflow_exchange_name() is not None:
        summary["workflow_exchange"] = topology.workflow_exchange_name()
        summary["workflow_stages"] = [
            {
                "name": stage,
                "queue": topology.workflow_queue_name(stage),
                "binding_keys": list(topology.workflow_binding_keys(stage)),
                "publish_routing_key": topology.workflow_publish_routing_key(stage),
            }
            for stage in topology.workflow_stage_names()
        ]
    else:
        summary["task_queue"] = topology.task_queue_name()
        summary["task_binding_keys"] = list(topology.task_binding_keys())
    return summary


__all__ = ["summarize_topology", "validate_topology"]
