from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..contracts import ActionSchema
from .workflow import SharedStatusWorkflowTopology, WorkflowStage


class WorkflowContractError(ValueError):
    def __init__(
        self,
        *,
        reason: str,
        message: str,
        stage: str | None = None,
        source_stage: str | None = None,
        target_stage: str | None = None,
        action: str | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.stage = stage
        self.source_stage = source_stage
        self.target_stage = target_stage
        self.action = action


def serialize_action_schemas(actions: tuple[ActionSchema, ...]) -> list[dict[str, Any]]:
    return [action.model_dump(mode="json", exclude_none=True) for action in actions]


def serialize_workflow_stage(stage: WorkflowStage, *, queue_arguments: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": stage.name,
        "name": stage.name,
        "queue": stage.queue,
        "binding_keys": list(stage.binding_keys),
        "publish_routing_key": stage.publish_routing_key,
        "queue_arguments": dict(queue_arguments),
        "description": stage.description,
        "role": stage.role,
        "owner": stage.owner,
        "tags": list(stage.tags),
        "sla_ms": stage.sla_ms,
        "accepted_actions": serialize_action_schemas(stage.accepted_actions),
        "produced_actions": serialize_action_schemas(stage.produced_actions),
        "allowed_next_stages": list(stage.allowed_next_stages),
        "terminal": stage.terminal,
        "timeout_seconds": stage.timeout_seconds,
        "max_retries": stage.max_retries,
        "retry_delay_ms": stage.retry_delay_ms,
        "max_inflight": stage.max_inflight,
        "dedup_key_fields": list(stage.dedup_key_fields),
    }


def validate_publish_contract(
    topology: SharedStatusWorkflowTopology,
    *,
    target_stage: str,
    action: str | None,
    payload: Mapping[str, Any],
    source_stage: str | None = None,
) -> None:
    destination = topology.workflow_stage(target_stage)
    if source_stage:
        source = topology.workflow_stage(source_stage)
        if source.terminal:
            raise WorkflowContractError(
                reason="terminal_stage_handoff",
                message=f"Workflow stage '{source_stage}' is terminal and cannot publish downstream handoffs",
                stage=source_stage,
                source_stage=source_stage,
                target_stage=target_stage,
                action=action,
            )
        if source.allowed_next_stages and target_stage not in source.allowed_next_stages:
            raise WorkflowContractError(
                reason="forbidden_transition",
                message=f"Workflow stage '{source_stage}' cannot publish to '{target_stage}'",
                stage=source_stage,
                source_stage=source_stage,
                target_stage=target_stage,
                action=action,
            )
        _validate_action_contract(
            source.produced_actions,
            action=action,
            payload=payload,
            stage_name=source_stage,
            unsupported_reason="unsupported_action",
        )

    _validate_action_contract(
        destination.accepted_actions,
        action=action,
        payload=payload,
        stage_name=target_stage,
        unsupported_reason="unsupported_action",
    )


def validate_inbound_message_contract(
    topology: SharedStatusWorkflowTopology,
    *,
    stage: str,
    action: str | None,
    payload: Mapping[str, Any],
) -> None:
    destination = topology.workflow_stage(stage)
    _validate_action_contract(
        destination.accepted_actions,
        action=action,
        payload=payload,
        stage_name=stage,
        unsupported_reason="unsupported_action",
    )


def _validate_action_contract(
    actions: tuple[ActionSchema, ...],
    *,
    action: str | None,
    payload: Mapping[str, Any],
    stage_name: str,
    unsupported_reason: str,
) -> None:
    if not actions:
        return
    action_name = str(action or "").strip()
    if not action_name:
        raise WorkflowContractError(
            reason=unsupported_reason,
            message=f"Workflow stage '{stage_name}' requires an action for this message",
            stage=stage_name,
            action=action,
        )

    schema = _schema_for_action(actions, action_name)
    if schema is None:
        raise WorkflowContractError(
            reason=unsupported_reason,
            message=f"Workflow stage '{stage_name}' does not support action '{action_name}'",
            stage=stage_name,
            action=action_name,
        )

    errors = schema.validate_payload(payload)
    if errors:
        raise WorkflowContractError(
            reason="payload_schema_violation",
            message=f"Workflow stage '{stage_name}' rejected action '{action_name}': {'; '.join(errors)}",
            stage=stage_name,
            action=action_name,
        )


def _schema_for_action(actions: tuple[ActionSchema, ...], action: str) -> ActionSchema | None:
    for item in actions:
        if item.action == action:
            return item
    return None


__all__ = [
    "WorkflowContractError",
    "serialize_action_schemas",
    "serialize_workflow_stage",
    "validate_inbound_message_contract",
    "validate_publish_contract",
]
