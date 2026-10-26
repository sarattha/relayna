"""Studio-only deterministic triage groups; no worker/runtime contracts."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

_UUID = re.compile(r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b", re.I)
_HEX = re.compile(r"\b(?:0x[0-9a-f]+|[0-9a-f]{16,})\b", re.I)
_NUMBER = re.compile(r"(?<![\w])[-+]?\d+(?:\.\d+)?(?![\w])")


def _dimensions(environment: str, service_id: str, item: dict[str, Any]) -> dict[str, str]:
    message = str(item.get("error_message") or "").strip()
    signature = " ".join(_NUMBER.sub("<number>", _HEX.sub("<id>", _UUID.sub("<id>", message))).split())
    return {
        "signature_version": "1",
        "environment": environment,
        "service_id": service_id,
        "queue_name": str(item.get("queue_name") or item.get("source_queue_name") or "unknown"),
        "stage": str(item.get("stage") or "unknown"),
        "error_type": str(item.get("error_type") or "unknown"),
        # No message is not evidence that two failures have a common cause.
        "signature": signature or f"Missing error message ({item['failure_id']})",
    }


def _group_id(dimensions: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(dimensions, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def _timestamp(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(UTC)
    except (ValueError, TypeError, OverflowError):
        return None


def _resolve(incident_id: str, states: dict[str, dict[str, Any]]) -> str:
    visited: set[str] = set()
    while states.get(incident_id, {}).get("merged_into"):
        if incident_id in visited:
            raise RuntimeError("Invalid incident merge cycle.")
        visited.add(incident_id)
        incident_id = states[incident_id]["merged_into"]
    return incident_id


def _summarize(state: dict[str, Any], evidence: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    dated = [(item, _timestamp(item.get("failed_at"))) for item in evidence]
    times = [stamp for _, stamp in dated if stamp is not None]
    recent = sum(1 for stamp in times if now - timedelta(hours=1) <= stamp <= now)
    preceding = sum(1 for stamp in times if now - timedelta(hours=2) <= stamp < now - timedelta(hours=1))
    return {
        **state,
        "failure_count": len(evidence),
        "affected_tasks": len({item["task_id"] for item in evidence if item.get("task_id")}),
        "missing_task_ids": sum(1 for item in evidence if not item.get("task_id")),
        "first_seen": min(times).isoformat() if times else None,
        "last_seen": max(times).isoformat() if times else None,
        "trend": {
            "recent_hour": recent,
            "preceding_hour": preceding,
            "unknown_timestamps": len(evidence) - len(times),
            "outside_two_hours": len(times) - recent - preceding,
        },
        "representative": evidence[:3],
    }
