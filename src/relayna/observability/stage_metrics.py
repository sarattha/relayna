from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class StageHealthSnapshot:
    stage: str
    received: int = 0
    published: int = 0
    failed: int = 0


def compute_stage_health(events: list[object]) -> dict[str, StageHealthSnapshot]:
    snapshots: dict[str, StageHealthSnapshot] = {}
    for event in events:
        stage = getattr(event, "stage", None)
        if not stage:
            continue
        snapshot = snapshots.setdefault(stage, StageHealthSnapshot(stage=stage))
        event_type = type(event).__name__
        if "Received" in event_type or "Started" in event_type:
            snapshot.received += 1
        if "Published" in event_type or "Acked" in event_type:
            snapshot.published += 1
        if "Failed" in event_type:
            snapshot.failed += 1
    return snapshots


__all__ = ["StageHealthSnapshot", "compute_stage_health"]
