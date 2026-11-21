from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..contracts import TaskEnvelope, normalize_contract_aliases


def spooltail_task_event(task: TaskEnvelope | Mapping[str, Any]) -> dict[str, Any]:
    """Serialize a Relayna task for the native client's publish operation."""
    envelope = (
        task
        if isinstance(task, TaskEnvelope)
        else TaskEnvelope.model_validate(normalize_contract_aliases(task, drop_aliases=True))
    )
    if envelope.priority is not None:
        raise ValueError("Spooltail does not support task priority")
    payload = envelope.model_dump(mode="json", exclude_none=True)
    payload.setdefault("event_id", "task")
    return payload
