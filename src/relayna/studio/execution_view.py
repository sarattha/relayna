from __future__ import annotations

from ..observability import ExecutionGraph, execution_graph_mermaid


def build_execution_view(graph: ExecutionGraph) -> dict[str, object]:
    return {
        "task_id": graph.task_id,
        "topology_kind": graph.topology_kind,
        "summary": graph.summary.model_dump(mode="json"),
        "related_task_ids": list(graph.related_task_ids),
        "graph": graph.model_dump(mode="json"),
        "mermaid": execution_graph_mermaid(graph),
    }


__all__ = ["build_execution_view"]
