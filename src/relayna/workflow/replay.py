from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True, frozen=True)
class ReplayRequest:
    task_id: str
    stage: str
    reason: str
    meta: dict[str, Any] = field(default_factory=dict)


def build_replay_headers(request: ReplayRequest) -> dict[str, Any]:
    return {
        "x-relayna-replay-task-id": request.task_id,
        "x-relayna-replay-stage": request.stage,
        "x-relayna-replay-reason": request.reason,
        "x-relayna-replay-meta": dict(request.meta),
    }


__all__ = ["ReplayRequest", "build_replay_headers"]
