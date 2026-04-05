from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True, frozen=True)
class LineageEdge:
    parent_message_id: str | None
    message_id: str
    source_stage: str | None
    target_stage: str | None


def build_lineage(messages: list[dict[str, Any]]) -> list[LineageEdge]:
    return [
        LineageEdge(
            parent_message_id=item.get("correlation_id"),
            message_id=str(item.get("message_id", "")),
            source_stage=item.get("origin_stage"),
            target_stage=item.get("stage"),
        )
        for item in messages
        if item.get("message_id")
    ]


__all__ = ["LineageEdge", "build_lineage"]
