from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from .workflow import SharedStatusWorkflowTopology
from .workflow_contract import serialize_workflow_stage


def export_workflow_graph(topology: SharedStatusWorkflowTopology) -> dict[str, Any]:
    """Exports a workflow topology into a frontend-friendly graph payload."""

    stages = []
    edges: list[dict[str, str]] = []
    for stage in topology.workflow_stage_names():
        stages.append(
            serialize_workflow_stage(
                topology.workflow_stage(stage),
                queue_arguments=topology.workflow_queue_arguments(stage),
            )
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
    stage_node_ids = _build_mermaid_node_ids((stage["id"] for stage in graph["stages"]), prefix="stage")
    entry_node_ids = _build_mermaid_node_ids((route["name"] for route in graph["entry_routes"]), prefix="entry")
    lines = ["flowchart LR"]
    for stage in graph["stages"]:
        node_id = stage_node_ids[stage["id"]]
        lines.append(f'    {node_id}["{stage["id"]}\\n{stage["queue"]}"]')
    for route in graph["entry_routes"]:
        node_id = entry_node_ids[route["name"]]
        target_stage = stage_node_ids[route["target_stage"]]
        lines.append(f'    {node_id}["{route["name"]}\\n{route["routing_key"]}"] --> {target_stage}')
    for edge in graph["edges"]:
        source = stage_node_ids[edge["source"]]
        target = stage_node_ids[edge["target"]]
        lines.append(f"    {source} -->|{edge['routing_key']}| {target}")
    return "\n".join(lines)


def _build_mermaid_node_ids(names: Iterable[str], *, prefix: str) -> dict[str, str]:
    identifiers: dict[str, str] = {}
    used: set[str] = set()
    for name in names:
        base = f"{prefix}_{_sanitize_mermaid_identifier(name)}"
        candidate = base
        suffix = 2
        while candidate in used:
            candidate = f"{base}_{suffix}"
            suffix += 1
        identifiers[name] = candidate
        used.add(candidate)
    return identifiers


def _sanitize_mermaid_identifier(name: str) -> str:
    sanitized = re.sub(r"[^0-9A-Za-z_]", "_", name).strip("_")
    return sanitized or "node"


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
