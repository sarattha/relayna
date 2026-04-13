from __future__ import annotations

from ..dlq.models import DLQMessageSummary
from ..topology.workflow import SharedStatusWorkflowTopology
from ..topology.workflow_graph import export_workflow_graph
from ..workflow.run_state import WorkflowRunState


def adapt_topology(topology: SharedStatusWorkflowTopology) -> dict[str, object]:
    return {"type": "topology", "value": export_workflow_graph(topology)}


def adapt_run_state(run_state: WorkflowRunState) -> dict[str, object]:
    return {
        "type": "workflow_run",
        "task_id": run_state.task_id,
        "status": run_state.status,
        "current_stage": run_state.current_stage,
    }


def adapt_dlq_summary(message: DLQMessageSummary) -> dict[str, object]:
    return {"type": "dlq_message", **message.model_dump(mode="json")}


__all__ = ["adapt_dlq_summary", "adapt_run_state", "adapt_topology"]
