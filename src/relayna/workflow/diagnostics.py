from __future__ import annotations

from dataclasses import dataclass, field

from .run_state import WorkflowRunState


@dataclass(slots=True)
class WorkflowDiagnosis:
    task_id: str
    status: str
    current_stage: str | None
    reasons: list[str] = field(default_factory=list)


def explain_stall(run_state: WorkflowRunState) -> WorkflowDiagnosis:
    reasons: list[str] = []
    if run_state.current_stage is None:
        reasons.append("workflow has not entered a stage yet")
    for stage_name, stage_state in run_state.stages.items():
        if stage_state.status in {"failed", "retrying"}:
            reasons.append(f"stage '{stage_name}' is {stage_state.status}")
    if not reasons and run_state.status not in {"completed", "failed"}:
        reasons.append("no terminal stage observed yet")
    return WorkflowDiagnosis(
        task_id=run_state.task_id,
        status=run_state.status,
        current_stage=run_state.current_stage,
        reasons=reasons,
    )


__all__ = ["WorkflowDiagnosis", "explain_stall"]
