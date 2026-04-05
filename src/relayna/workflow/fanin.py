from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class FanInProgress:
    stage: str
    expected: set[str] = field(default_factory=set)
    completed: set[str] = field(default_factory=set)

    @property
    def is_complete(self) -> bool:
        return bool(self.expected) and self.completed >= self.expected


def update_fanin_progress(progress: FanInProgress, *, completed_stage: str) -> FanInProgress:
    progress.completed.add(completed_stage)
    return progress


__all__ = ["FanInProgress", "update_fanin_progress"]
