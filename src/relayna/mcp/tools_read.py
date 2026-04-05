from __future__ import annotations

from ..dlq.service import DLQService
from ..topology.workflow import SharedStatusWorkflowTopology
from ..topology.workflow_graph import export_workflow_graph
from ..workflow.diagnostics import explain_stall
from ..workflow.run_state import WorkflowRunState


def inspect_topology(topology: SharedStatusWorkflowTopology) -> dict[str, object]:
    return export_workflow_graph(topology)


def explain_workflow(run_state: WorkflowRunState) -> dict[str, object]:
    diagnosis = explain_stall(run_state)
    return {
        "task_id": diagnosis.task_id,
        "status": diagnosis.status,
        "current_stage": diagnosis.current_stage,
        "reasons": diagnosis.reasons,
    }


async def list_dlq_messages(service: DLQService, *, queue_name: str | None = None) -> dict[str, object]:
    payload = await service.list_messages(queue_name=queue_name, limit=50)
    return payload.model_dump(mode="json")


__all__ = ["explain_workflow", "inspect_topology", "list_dlq_messages"]
