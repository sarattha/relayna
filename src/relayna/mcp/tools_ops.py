from __future__ import annotations

from ..dlq.service import DLQService
from ..workflow.replay import ReplayRequest


async def replay_dlq(service: DLQService, dlq_id: str, *, force: bool = False):
    return await service.replay_message(dlq_id, force=force)


def resume_workflow(request: ReplayRequest) -> dict[str, object]:
    return {
        "task_id": request.task_id,
        "stage": request.stage,
        "reason": request.reason,
        "meta": dict(request.meta),
    }


__all__ = ["replay_dlq", "resume_workflow"]
