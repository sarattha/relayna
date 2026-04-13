from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class WorkflowStageState:
    stage: str
    status: str = "pending"
    attempts: int = 0
    latest_message_id: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class WorkflowRunState:
    task_id: str
    current_stage: str | None = None
    status: str = "pending"
    stages: dict[str, WorkflowStageState] = field(default_factory=dict)

    def update_stage(
        self, stage: str, *, status: str, message_id: str | None = None, meta: dict[str, Any] | None = None
    ) -> None:
        item = self.stages.setdefault(stage, WorkflowStageState(stage=stage))
        item.status = status
        item.attempts += 1
        item.latest_message_id = message_id
        if meta:
            item.meta.update(meta)
        self.current_stage = stage
        self.status = status


__all__ = ["WorkflowRunState", "WorkflowStageState"]
