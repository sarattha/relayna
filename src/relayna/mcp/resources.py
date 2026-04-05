from __future__ import annotations

from ..dlq.models import DLQMessageSummary
from ..topology.workflow import SharedStatusWorkflowTopology
from ..workflow.run_state import WorkflowRunState
from .adapters import adapt_dlq_summary, adapt_run_state, adapt_topology


def list_runtime_resources(
    *,
    topology: SharedStatusWorkflowTopology | None = None,
    run_states: list[WorkflowRunState] | None = None,
    dlq_messages: list[DLQMessageSummary] | None = None,
) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    if topology is not None:
        items.append(adapt_topology(topology))
    for run_state in run_states or []:
        items.append(adapt_run_state(run_state))
    for message in dlq_messages or []:
        items.append(adapt_dlq_summary(message))
    return items


__all__ = ["list_runtime_resources"]
