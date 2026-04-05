from __future__ import annotations

from typing import Any

from .workflow import SharedStatusWorkflowTopology


def export_workflow_graph(topology: SharedStatusWorkflowTopology) -> dict[str, Any]:
    """Exports a workflow topology into a frontend-friendly graph payload."""

    stages = []
    edges: list[dict[str, str]] = []
    for stage in topology.workflow_stage_names():
        stages.append(
            {
                "id": stage,
                "queue": topology.workflow_queue_name(stage),
                "binding_keys": list(topology.workflow_binding_keys(stage)),
                "publish_routing_key": topology.workflow_publish_routing_key(stage),
                "queue_arguments": topology.workflow_queue_arguments(stage),
            }
        )

    for source in topology.workflow_stage_names():
        routing_key = topology.workflow_publish_routing_key(source)
        for destination in topology.workflow_stage_names():
            if any(
                _topic_binding_matches(binding, routing_key) for binding in topology.workflow_binding_keys(destination)
            ):
                edges.append({"source": source, "target": destination, "routing_key": routing_key})

    entry_routes = [
        {
            "name": route.name,
            "routing_key": route.routing_key,
            "target_stage": route.target_stage,
        }
        for route in topology.entry_routes
    ]
    return {
        "workflow_exchange": topology.workflow_exchange_name(),
        "status_queue": topology.status_queue_name(),
        "stages": stages,
        "entry_routes": entry_routes,
        "edges": edges,
    }


def workflow_graph_mermaid(topology: SharedStatusWorkflowTopology) -> str:
    graph = export_workflow_graph(topology)
    lines = ["flowchart LR"]
    for stage in graph["stages"]:
        lines.append(f'    {stage["id"]}["{stage["id"]}\\n{stage["queue"]}"]')
    for route in graph["entry_routes"]:
        node_id = f"entry_{route['name'].replace('-', '_')}"
        lines.append(f'    {node_id}["{route["name"]}\\n{route["routing_key"]}"] --> {route["target_stage"]}')
    for edge in graph["edges"]:
        lines.append(f"    {edge['source']} -->|{edge['routing_key']}| {edge['target']}")
    return "\n".join(lines)


def _topic_binding_matches(binding_key: str, routing_key: str) -> bool:
    binding_parts = binding_key.split(".")
    routing_parts = routing_key.split(".")
    return _matches_parts(binding_parts, routing_parts)


def _matches_parts(
    binding_parts: list[str], routing_parts: list[str], binding_index: int = 0, routing_index: int = 0
) -> bool:
    while True:
        if binding_index == len(binding_parts):
            return routing_index == len(routing_parts)
        token = binding_parts[binding_index]
        if token == "#":
            if binding_index == len(binding_parts) - 1:
                return True
            return any(
                _matches_parts(binding_parts, routing_parts, binding_index + 1, next_routing)
                for next_routing in range(routing_index, len(routing_parts) + 1)
            )
        if routing_index == len(routing_parts):
            return False
        if token not in ("*", routing_parts[routing_index]):
            return False
        binding_index += 1
        routing_index += 1


__all__ = ["export_workflow_graph", "workflow_graph_mermaid"]
