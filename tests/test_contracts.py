from relayna.contracts import (
    ContractAliasConfig,
    StatusEventEnvelope,
    TerminalStatusSet,
    denormalize_document_aliases,
    public_output_aliases,
    normalize_contract_aliases,
    ensure_status_event_id,
    normalize_event_aliases,
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
