from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from .aliases import ContractAliasConfig, normalize_contract_aliases


def normalize_task_collection(
    tasks: list[Mapping[str, Any]],
    alias_config: ContractAliasConfig | None = None,
) -> list[dict[str, Any]]:
    return [normalize_contract_aliases(task, alias_config) for task in tasks]


def is_batch_task_payload(payload: Mapping[str, Any]) -> bool:
    tasks = payload.get("tasks")
    return isinstance(payload.get("batch_id"), str) and isinstance(tasks, list)


def ensure_status_event_id(event: Mapping[str, Any]) -> dict[str, Any]:
    """Ensures status events have a deterministic event_id when one is not supplied."""

    data = dict(event.items())
    existing = data.get("event_id")
    if isinstance(existing, str):
        existing = existing.strip()
        if existing:
            data["event_id"] = existing
            return data

    canonical = dict(data)
    canonical.pop("event_id", None)
    digest = hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=_json_default).encode(
            "utf-8"
        )
    ).hexdigest()
    data["event_id"] = digest
    return data


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


__all__ = ["ensure_status_event_id", "is_batch_task_payload", "normalize_task_collection"]
