from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, Field


class PayloadSchema(BaseModel):
    """Declarative payload requirements for workflow actions and API contracts."""

    name: str
    version: str = "v1"
    required_fields: tuple[str, ...] = ()
    optional_fields: tuple[str, ...] = ()

    def validate_payload(self, payload: Mapping[str, Any]) -> list[str]:
        missing = [field for field in self.required_fields if field not in payload]
        unknown = sorted(set(payload).difference(self.required_fields).difference(self.optional_fields))
        errors: list[str] = []
        if missing:
            errors.append(f"missing required fields: {', '.join(missing)}")
        if unknown:
            errors.append(f"unknown fields: {', '.join(unknown)}")
        return errors


class ActionSchema(BaseModel):
    """Workflow action contract metadata."""

    action: str
    description: str | None = None
    payload: PayloadSchema | None = None

    def validate_payload(self, payload: Mapping[str, Any]) -> list[str]:
        if self.payload is None:
            return []
        return self.payload.validate_payload(payload)


class WorkflowActionSchema(BaseModel):
    """Collection of supported workflow action contracts."""

    actions: Sequence[ActionSchema] = Field(default_factory=tuple)

    def for_action(self, action: str) -> ActionSchema | None:
        for item in self.actions:
            if item.action == action:
                return item
        return None

    def validate_action(self, action: str, payload: Mapping[str, Any]) -> list[str]:
        schema = self.for_action(action)
        if schema is None:
            return [f"unsupported action '{action}'"]
        return schema.validate_payload(payload)


__all__ = ["ActionSchema", "PayloadSchema", "WorkflowActionSchema"]
