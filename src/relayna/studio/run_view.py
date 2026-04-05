from __future__ import annotations

from ..workflow.diagnostics import explain_stall
from ..workflow.run_state import WorkflowRunState


def build_run_view(run_state: WorkflowRunState) -> dict[str, object]:
    diagnosis = explain_stall(run_state)
    return {
        "task_id": run_state.task_id,
        "status": run_state.status,
        "current_stage": run_state.current_stage,
        "stages": {
            stage: {
                "status": item.status,
                "attempts": item.attempts,
                "latest_message_id": item.latest_message_id,
                "meta": dict(item.meta),
            }
            for stage, item in run_state.stages.items()
        },
        "diagnosis": diagnosis.reasons,
    }


__all__ = ["build_run_view"]
