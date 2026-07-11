from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.testclient import TestClient

from relayna.api.replay_routes import create_replay_router
from relayna.dlq import DLQReplayConflict, FailedTaskRetryRejected


class Result:
    def __init__(self, **payload: Any) -> None:
        self.payload = payload

    def model_dump(self, *, mode: str) -> dict[str, Any]:
        assert mode == "json"
        return jsonable_encoder(self.payload)


class Service:
    supports_broker_message_reads = True

    async def get_queue_summaries(self) -> list[Result]:
        return [Result(queue_name="queue.dlq")]

    async def get_broker_queue_summaries(self, names: Any) -> list[Result]:
        return [Result(queue_name=name) for name in names]

    async def list_broker_messages(self, names: Any, **kwargs: Any) -> Result:
        if kwargs["queue_name"] == "bad":
            raise ValueError("unknown queue")
        return Result(count=0, items=[], names=list(names), **kwargs)

    async def list_messages(self, **kwargs: Any) -> Result:
        return Result(count=0, items=[], **kwargs)

    async def replay_message(self, dlq_id: str, *, force: bool) -> Result | None:
        if dlq_id == "conflict":
            raise DLQReplayConflict(dlq_id)
        if dlq_id == "missing":
            return None
        return Result(dlq_id=dlq_id, force=force)

    async def get_message_detail(self, dlq_id: str) -> Result | None:
        return None if dlq_id == "missing" else Result(dlq_id=dlq_id)

    async def list_failed_tasks(self, **kwargs: Any) -> Result:
        return Result(count=0, items=[], **kwargs)

    async def get_failed_task_detail(self, failure_id: str) -> Result | None:
        return None if failure_id == "missing" else Result(failure_id=failure_id)

    async def mark_failed_task_investigated(self, failure_id: str, **kwargs: Any) -> Result | None:
        return None if failure_id == "missing" else Result(failure_id=failure_id, **kwargs)

    async def mark_failed_task_uninvestigated(self, failure_id: str) -> Result | None:
        return None if failure_id == "missing" else Result(failure_id=failure_id)

    async def retry_failed_task(self, failure_id: str, request: Any) -> Result | None:
        if failure_id == "rejected":
            raise FailedTaskRetryRejected(failure_id, code="not_retryable", detail="cannot retry")
        if failure_id == "missing":
            return None
        return Result(failure_id=failure_id, request=request.model_dump(mode="json") if request else None)

    async def delete_failed_task(self, failure_id: str) -> bool:
        return failure_id != "missing"


def test_replay_router_all_success_and_error_paths() -> None:
    app = FastAPI()
    app.include_router(
        create_replay_router(
            dlq_service=Service(),  # type: ignore[arg-type]
            broker_dlq_queue_names=["queue.dlq"],
        )
    )
    client = TestClient(app)

    assert client.get("/dlq/queues").status_code == 200
    assert client.get("/broker/dlq/queues").status_code == 200
    assert client.get("/broker/dlq/messages", params={"queue_name": "queue.dlq"}).status_code == 200
    assert client.get("/broker/dlq/messages", params={"queue_name": "bad"}).status_code == 400
    assert (
        client.get(
            "/dlq/messages",
            params={
                "queue_name": "queue.dlq",
                "task_id": "task",
                "reason": "handler_error",
                "source_queue_name": "queue",
                "state": "dead_lettered",
                "cursor": "cursor",
                "limit": 1,
            },
        ).status_code
        == 200
    )

    assert client.post("/dlq/messages/dlq-1/replay", params={"force": True}).status_code == 200
    assert client.post("/dlq/messages/conflict/replay").status_code == 409
    assert client.post("/dlq/messages/missing/replay").status_code == 404
    assert client.get("/dlq/messages/dlq-1").status_code == 200
    assert client.get("/dlq/messages/missing").status_code == 404

    assert (
        client.get(
            "/failed-tasks",
            params={
                "service_name": "service",
                "queue_name": "queue",
                "dlq_name": "queue.dlq",
                "error_type": "RuntimeError",
                "status": "failed",
                "task_id": "task",
                "worker_id": "worker",
                "investigation_status": "unreviewed",
                "failed_from": "2026-01-01T00:00:00Z",
                "failed_to": "2026-01-02T00:00:00Z",
                "cursor": "cursor",
                "limit": 1,
            },
        ).status_code
        == 200
    )
    assert client.get("/failed-tasks/failure").status_code == 200
    assert client.get("/failed-tasks/missing").status_code == 404

    assert (
        client.post(
            "/failed-tasks/failure/mark-investigated",
            json={"investigated_by": "admin", "note": "known"},
        ).status_code
        == 200
    )
    assert client.post("/failed-tasks/failure/mark-investigated").status_code == 200
    assert client.post("/failed-tasks/missing/mark-investigated").status_code == 404
    assert client.post("/failed-tasks/failure/mark-uninvestigated").status_code == 200
    assert client.post("/failed-tasks/missing/mark-uninvestigated").status_code == 404

    assert client.post("/failed-tasks/failure/retry").status_code == 200
    assert client.post("/failed-tasks/rejected/retry").status_code == 409
    assert client.post("/failed-tasks/missing/retry").status_code == 404
    assert client.delete("/failed-tasks/failure").status_code == 204
    assert client.delete("/failed-tasks/missing").status_code == 404
