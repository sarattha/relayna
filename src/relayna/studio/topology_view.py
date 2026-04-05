from __future__ import annotations

from ..topology.workflow import SharedStatusWorkflowTopology
from ..topology.workflow_graph import export_workflow_graph


def build_topology_view(topology: SharedStatusWorkflowTopology) -> dict[str, object]:
    graph = export_workflow_graph(topology)
    return {
        "graph": graph,
        "stage_count": len(graph["stages"]),
        "entry_route_count": len(graph["entry_routes"]),
    }


__all__ = ["build_topology_view"]
