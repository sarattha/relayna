from __future__ import annotations

from ..dlq.models import DLQMessageSummary, DLQQueueSummary


def build_dlq_view(queues: list[DLQQueueSummary], messages: list[DLQMessageSummary]) -> dict[str, object]:
    return {
        "queues": [item.model_dump(mode="json") for item in queues],
        "messages": [item.model_dump(mode="json") for item in messages],
    }


__all__ = ["build_dlq_view"]
