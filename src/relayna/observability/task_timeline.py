from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class TimelineEntry:
    event_type: str
    task_id: str | None
    stage: str | None
    timestamp: object | None


def build_task_timeline(events: list[object], *, task_id: str) -> list[TimelineEntry]:
    items: list[TimelineEntry] = []
    for event in events:
        current_task_id = getattr(event, "task_id", None)
        if current_task_id != task_id:
            continue
        items.append(
            TimelineEntry(
                event_type=type(event).__name__,
                task_id=current_task_id,
                stage=getattr(event, "stage", None),
                timestamp=getattr(event, "timestamp", None),
            )
        )
    return sorted(items, key=lambda item: (str(item.timestamp), item.event_type))


__all__ = ["TimelineEntry", "build_task_timeline"]
