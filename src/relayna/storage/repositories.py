from __future__ import annotations

from typing import Protocol

from ..workflow.run_state import WorkflowRunState


class RunStateRepository(Protocol):
    async def get(self, task_id: str) -> WorkflowRunState | None: ...

    async def put(self, state: WorkflowRunState) -> None: ...


__all__ = ["RunStateRepository"]
