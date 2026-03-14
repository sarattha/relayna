from relayna.contracts import (
    StatusEventEnvelope,
    TerminalStatusSet,
    denormalize_document_aliases,
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
