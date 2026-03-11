from relayna.contracts import TerminalStatusSet, denormalize_document_aliases, normalize_event_aliases


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
