from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


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


def _to_mutable_dict(event: Mapping[str, Any]) -> dict[str, Any]:
    return dict(event.items())


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


__all__ = [
    "ContractAliasConfig",
    "denormalize_contract_aliases",
    "denormalize_document_aliases",
    "normalize_contract_aliases",
    "normalize_event_aliases",
    "public_output_aliases",
]
