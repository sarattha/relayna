import pytest
from pydantic import ValidationError

from relayna.contracts import (
    ContractAliasConfig,
    StatusEventEnvelope,
    TaskEnvelope,
    TerminalStatusSet,
    WorkflowEnvelope,
    denormalize_document_aliases,
    ensure_status_event_id,
    normalize_contract_aliases,
    normalize_event_aliases,
    public_output_aliases,
)


def test_normalize_aliases_maps_document_id_to_task_id() -> None:
    data = normalize_event_aliases({"documentId": "doc-123", "status": "ready"})
    assert data["task_id"] == "doc-123"
    assert data["documentId"] == "doc-123"


def test_denormalize_keeps_document_id() -> None:
    data = denormalize_document_aliases({"task_id": "doc-123", "status": "ready"})
    assert data["documentId"] == "doc-123"


def test_normalize_contract_aliases_maps_custom_alias_to_task_id() -> None:
    config = ContractAliasConfig(field_aliases={"task_id": "attempt_id"})

    data = normalize_contract_aliases({"attempt_id": "attempt-123", "status": "ready"}, config)

    assert data["task_id"] == "attempt-123"


def test_public_output_aliases_omits_canonical_field_when_alias_configured() -> None:
    config = ContractAliasConfig(field_aliases={"task_id": "attempt_id"})

    data = public_output_aliases({"task_id": "attempt-123", "status": "ready"}, config)

    assert data == {"attempt_id": "attempt-123", "status": "ready"}


def test_terminal_status_set() -> None:
    terminal = TerminalStatusSet({"completed", "failed"})
    assert terminal.is_terminal("completed") is True
    assert terminal.is_terminal("queued") is False


def test_ensure_status_event_id_is_deterministic_when_missing() -> None:
    event = {"task_id": "task-123", "status": "processing", "message": "Started."}

    first = ensure_status_event_id(event)
    second = ensure_status_event_id(event)

    assert first["event_id"] == second["event_id"]
    assert isinstance(first["event_id"], str)
    assert len(first["event_id"]) == 64


def test_status_event_transport_dict_adds_correlation_id_and_event_id() -> None:
    event = StatusEventEnvelope(task_id="task-123", status="processing", message="Started.")

    transport = event.as_transport_dict()

    assert transport["correlation_id"] == "task-123"
    assert isinstance(transport["event_id"], str)
    assert len(transport["event_id"]) == 64


def test_workflow_envelope_defaults_message_id_and_correlation_id() -> None:
    event = WorkflowEnvelope(task_id="task-123", stage="planner")

    transport = event.as_transport_dict()

    assert transport["task_id"] == "task-123"
    assert transport["stage"] == "planner"
    assert transport["correlation_id"] == "task-123"
    assert isinstance(transport["message_id"], str)
    assert transport["message_id"]


def test_task_envelope_accepts_priority_in_amqp_range() -> None:
    event = TaskEnvelope(task_id="task-123", priority=255)

    transport = event.model_dump(mode="json", exclude_none=True)

    assert transport["priority"] == 255


@pytest.mark.parametrize("priority", [-1, 256, "high"])
def test_task_envelope_rejects_invalid_priority(priority: object) -> None:
    with pytest.raises(ValidationError):
        TaskEnvelope(task_id="task-123", priority=priority)


def test_workflow_envelope_accepts_priority_in_amqp_range() -> None:
    event = WorkflowEnvelope(task_id="task-123", stage="planner", priority=0)

    transport = event.as_transport_dict()

    assert transport["priority"] == 0


@pytest.mark.parametrize("priority", [-1, 256, "high"])
def test_workflow_envelope_rejects_invalid_priority(priority: object) -> None:
    with pytest.raises(ValidationError):
        WorkflowEnvelope(task_id="task-123", stage="planner", priority=priority)
