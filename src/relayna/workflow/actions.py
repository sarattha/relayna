from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..contracts.schemas import ActionSchema


@dataclass(slots=True, frozen=True)
class WorkflowAction:
    name: str
    payload: dict[str, Any] = field(default_factory=dict)


def validate_action_payload(action: WorkflowAction, schema: ActionSchema | None) -> list[str]:
    if schema is None:
        return []
    return schema.validate_payload(action.payload)


__all__ = ["WorkflowAction", "validate_action_payload"]
