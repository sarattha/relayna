from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from relayna.api.capabilities_routes import (
    build_legacy_fallback_capability_document,
    merge_capability_route_ids,
    relayna_version,
)
from relayna.api.status_routes import _status_value, create_status_router
from relayna.status import SSEStatusStream


def test_capability_merge_invalid_duplicate_fallback_and_version(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="Unsupported capability"):
        merge_capability_route_ids(["unknown.route"])
    assert merge_capability_route_ids(["status.events", "status.events"]) == ("status.events",)
    monkeypatch.setattr(
        "relayna.api.capabilities_routes.version",
        lambda _name: (_ for _ in ()).throw(__import__("importlib.metadata").metadata.PackageNotFoundError),
    )
    assert relayna_version() == "0.0.0"
    fallback = build_legacy_fallback_capability_document(capability_path="/custom")
    assert fallback.service_metadata.discovery_source == "fallback"
    assert fallback.service_metadata.capability_path == "/custom"


def test_status_router_latest_retry_history_success_errors_and_event_stream() -> None:
    class Latest:
        def __init__(self) -> None:
            self.calls = 0

        async def get_latest(self, task_id: str) -> dict[str, Any] | None:
            self.calls += 1
            if task_id == "missing":
                return None
            if task_id == "eventual":
                return {"task_id": task_id, "status": "processing" if self.calls == 1 else "completed"}
            return {"task_id": task_id, "status": 7}

    class History:
        async def replay(self, **kwargs: Any) -> list[dict[str, Any]]:
            if kwargs["task_id"] == "error":
                raise RuntimeError("not a stream")
            return [{"task_id": kwargs["task_id"], "status": "done"}]

    class Stream:
        async def stream(self, task_id: str, *, last_event_id: str | None = None) -> AsyncIterator[bytes]:
            yield f"data: {task_id}:{last_event_id}\n\n".encode()

    latest = Latest()
    app = FastAPI()
    app.include_router(
        create_status_router(
            sse_stream=Stream(),  # type: ignore[arg-type]
            history_reader=History(),  # type: ignore[arg-type]
            latest_status_store=latest,  # type: ignore[arg-type]
            latest_retry_attempts=2,
            latest_retry_delay_seconds=0,
        )
    )
    client = TestClient(app)
    assert client.get("/events/task", headers={"Last-Event-ID": "event"}).text == "data: task:event\n\n"
    assert client.get("/status/eventual").json()["event"]["status"] == "completed"
    assert client.get("/status/numeric").json()["event"]["status"] == 7
    assert client.get("/status/missing").status_code == 404
    assert client.get("/history").status_code == 422
    assert client.get("/history", params={"task_id": "error"}).status_code == 400
    response = client.get("/history", params={"task_id": "task", "start_offset": 7, "max_scan": 1})
    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert _status_value({"status": None}) is None
    assert _status_value({"status": 7}) == "7"

    assert SSEStatusStream
