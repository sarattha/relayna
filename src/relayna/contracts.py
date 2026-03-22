from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field


class TaskEnvelope(BaseModel):
    """Canonical task message shape for transport."""

    model_config = ConfigDict(extra="allow")

    task_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    service: str | None = None
    task_type: str | None = None
    correlation_id: str | None = None
    spec_version: str = "1.0"


class BatchTaskEnvelope(BaseModel):
    """Batch transport wrapper for task messages."""

    model_config = ConfigDict(extra="allow")

    batch_id: str
    tasks: list[TaskEnvelope] = Field(min_length=1)
    meta: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    spec_version: str = "1.0"


class StatusEventEnvelope(BaseModel):
    """Canonical status event shape used by shared infrastructure."""

    model_config = ConfigDict(extra="allow")

    task_id: str
    status: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    message: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    correlation_id: str | None = None
    event_id: str | None = None
    service: str | None = None
    spec_version: str = "1.0"

    def as_transport_dict(self) -> dict[str, Any]:
        data = self.model_dump(mode="json", exclude_none=True)
        data.setdefault("correlation_id", self.correlation_id or self.task_id)
        return ensure_status_event_id(data)


@dataclass(slots=True, frozen=True)
class ContractAliasConfig:
    """Alias configuration for payload and HTTP field names."""

    field_aliases: dict[str, str] = field(default_factory=dict)
    http_aliases: dict[str, str] = field(default_factory=dict)

    def payload_alias_for(self, field_name: str) -> str | None:
        alias = self.field_aliases.get(field_name)
        if isinstance(alias, str):
            alias = alias.strip()
            if alias:
                return alias
        return None

    def http_alias_for(self, field_name: str) -> str | None:
        alias = self.http_aliases.get(field_name)
        if not alias:
            alias = self.payload_alias_for(field_name)
        if isinstance(alias, str):
            alias = alias.strip()
            if alias:
                return alias
        return None


@dataclass(slots=True)
class TerminalStatusSet:
    """Defines statuses that should terminate stream subscriptions."""

    statuses: set[str] = field(default_factory=lambda: {"completed", "failed"})

    def is_terminal(self, status: str | None) -> bool:
        if not status:
            return False
        return status.lower() in {value.lower() for value in self.statuses}


def _to_mutable_dict(event: Mapping[str, Any]) -> dict[str, Any]:
    return dict(event.items())


DOCUMENT_COMPAT_ALIASES = ContractAliasConfig(field_aliases={"task_id": "documentId"})


def normalize_contract_aliases(
    event: Mapping[str, Any],
    alias_config: ContractAliasConfig | None = None,
    *,
    source: str = "payload",
    drop_aliases: bool = False,
) -> dict[str, Any]:
    """Normalizes configured aliases into canonical keys."""

    data = _to_mutable_dict(event)
    for canonical, alias in _alias_map(alias_config, source=source).items():
        if canonical in data:
            continue
        if alias in data:
            data[canonical] = data[alias]
    if drop_aliases:
        for canonical, alias in _alias_map(alias_config, source=source).items():
            if canonical in data:
                data.pop(alias, None)
    if "task_id" in data and data["task_id"] is not None:
        data["task_id"] = str(data["task_id"])
    return data


def denormalize_contract_aliases(
    event: Mapping[str, Any],
    alias_config: ContractAliasConfig | None = None,
    *,
    source: str = "payload",
    alias_only: bool = False,
) -> dict[str, Any]:
    """Applies configured aliases to canonical keys for public output."""

    data = _to_mutable_dict(event)
    for canonical, alias in _alias_map(alias_config, source=source).items():
        if canonical not in data:
            continue
        value = data[canonical]
        if value is None:
            continue
        data[alias] = value
        if alias_only:
            data.pop(canonical, None)
    return data


def normalize_event_aliases(
    event: Mapping[str, Any],
    alias_config: ContractAliasConfig | None = None,
) -> dict[str, Any]:
    """Backward-compatible payload alias normalization."""

    effective_aliases = alias_config or DOCUMENT_COMPAT_ALIASES
    return normalize_contract_aliases(event, effective_aliases, source="payload")


def denormalize_document_aliases(event: Mapping[str, Any]) -> dict[str, Any]:
    """Ensures document-oriented clients continue seeing documentId."""

    return denormalize_contract_aliases(event, DOCUMENT_COMPAT_ALIASES)


def public_output_aliases(
    event: Mapping[str, Any],
    alias_config: ContractAliasConfig | None = None,
    *,
    source: str = "payload",
) -> dict[str, Any]:
    """Returns the external/public shape for an event."""

    if alias_config is None:
        return _to_mutable_dict(event)
    return denormalize_contract_aliases(event, alias_config, source=source, alias_only=True)


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

    data = _to_mutable_dict(event)
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


def _alias_map(
    alias_config: ContractAliasConfig | None,
    *,
    source: str,
) -> dict[str, str]:
    if source == "payload":
        aliases = dict(DOCUMENT_COMPAT_ALIASES.field_aliases)
        if alias_config is not None:
            aliases.update(alias_config.field_aliases)
        return aliases
    if alias_config is None:
        return {}
    aliases = dict(alias_config.field_aliases)
    aliases.update(alias_config.http_aliases)
    return aliases
