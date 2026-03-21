from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from relayna.dlq import DLQRecord, DLQRecordState, build_dlq_record
from relayna.fastapi import create_dlq_router, create_status_router
from relayna.dlq import DLQService
import relayna.fastapi as relayna_fastapi
from relayna.topology import SharedTasksSharedStatusTopology


class FakeRabbitClient:
    instances: list["FakeRabbitClient"] = []
    fail_initialize = False

    def __init__(self, topology: SharedTasksSharedStatusTopology, *, connection_name: str) -> None:
        self.topology = topology
        self.connection_name = connection_name
        self.initialize_calls = 0
        self.close_calls = 0
        self.queue_counts: dict[str, int] = {}
        self.raw_queue_publishes: list[dict[str, object]] = []
        FakeRabbitClient.instances.append(self)

    async def initialize(self) -> None:
        self.initialize_calls += 1
        if self.fail_initialize:
            raise RuntimeError("rabbit init failed")

    async def close(self) -> None:
        self.close_calls += 1

    async def inspect_queue(self, queue_name: str):
        if queue_name not in self.queue_counts:
            return None

        class QueueInspection:
            def __init__(self, name: str, message_count: int) -> None:
                self.queue_name = name
                self.message_count = message_count
                self.consumer_count = 0

        return QueueInspection(queue_name, self.queue_counts[queue_name])

    async def publish_raw_to_queue(
        self,
        queue_name: str,
        body: bytes,
        *,
        correlation_id: str | None = None,
        headers: dict[str, object] | None = None,
        content_type: str | None = "application/json",
        delivery_mode: object | None = None,
    ) -> None:
        self.raw_queue_publishes.append(
            {
                "queue_name": queue_name,
                "body": body,
                "correlation_id": correlation_id,
                "headers": dict(headers or {}),
                "content_type": content_type,
                "delivery_mode": delivery_mode,
            }
        )


class FakeRedis:
    instances: list["FakeRedis"] = []

    def __init__(self, url: str) -> None:
        self.url = url
        self.close_calls = 0
        FakeRedis.instances.append(self)

    @classmethod
    def from_url(cls, url: str) -> "FakeRedis":
        return cls(url)

    async def aclose(self) -> None:
        self.close_calls += 1


class FakeStore:
    fail_init = False
    instances: list["FakeStore"] = []

    def __init__(
        self,
        redis: FakeRedis,
        *,
        prefix: str,
        ttl_seconds: int | None,
        history_maxlen: int,
    ) -> None:
        if self.fail_init:
            raise RuntimeError("store init failed")
        self.redis = redis
        self.prefix = prefix
        self.ttl_seconds = ttl_seconds
        self.history_maxlen = history_maxlen
        self.latest_by_task: dict[str, dict[str, object]] = {}
        self.history_by_task: dict[str, list[dict[str, object]]] = {}
        FakeStore.instances.append(self)

    async def get_latest(self, task_id: str) -> dict[str, object] | None:
        return self.latest_by_task.get(task_id)

    async def get_history(self, task_id: str, limit: int | None = None) -> list[dict[str, object]]:
        items = self.history_by_task.get(task_id, [])
        if limit is None:
            return list(items)
        return list(items[:limit])


class FakeHub:
    instances: list["FakeHub"] = []

    def __init__(
        self,
        *,
        rabbitmq: FakeRabbitClient,
        store: FakeStore,
        consume_arguments: dict[str, object] | None = None,
        sanitize_meta_keys: set[str] | None = None,
        prefetch: int = 200,
    ) -> None:
        self.rabbitmq = rabbitmq
        self.store = store
        self.consume_arguments = consume_arguments
        self.sanitize_meta_keys = sanitize_meta_keys
        self.prefetch = prefetch
        self.stop_calls = 0
        self.run_calls = 0
        self._stopped = asyncio.Event()
        FakeHub.instances.append(self)

    def stop(self) -> None:
        self.stop_calls += 1
        self._stopped.set()

    async def run_forever(self) -> None:
        self.run_calls += 1
        await self._stopped.wait()


class FakeSSEStatusStream:
    instances: list["FakeSSEStatusStream"] = []

    def __init__(
        self,
        *,
        store: FakeStore,
        terminal_statuses: object | None = None,
        output_adapter: object | None = None,
    ) -> None:
        self.store = store
        self.terminal_statuses = terminal_statuses
        self.output_adapter = output_adapter
        self.stream_calls: list[dict[str, str | None]] = []
        FakeSSEStatusStream.instances.append(self)

    async def stream(self, task_id: str, *, last_event_id: str | None = None):
        self.stream_calls.append({"task_id": task_id, "last_event_id": last_event_id})
        yield b"event: ready\ndata: {}\n\n"
        yield f'event: status\ndata: {{"task_id": "{task_id}", "status": "completed"}}\n\n'.encode()


class FakeHistoryReader:
    instances: list["FakeHistoryReader"] = []

    def __init__(
        self,
        *,
        rabbitmq: FakeRabbitClient,
        queue_arguments: dict[str, object] | None = None,
        output_adapter: object | None = None,
    ) -> None:
        self.rabbitmq = rabbitmq
        self.queue_arguments = queue_arguments
        self.output_adapter = output_adapter
        self.replay_calls: list[dict[str, object]] = []
        FakeHistoryReader.instances.append(self)

    async def replay(
        self,
        *,
        task_id: str | None = None,
        start_offset: str | int = "first",
        max_seconds: float | None = None,
        max_scan: int | None = None,
        require_stream: bool = True,
    ) -> list[dict[str, object]]:
        self.replay_calls.append(
            {
                "task_id": task_id,
                "start_offset": start_offset,
                "max_seconds": max_seconds,
                "max_scan": max_scan,
                "require_stream": require_stream,
            }
        )
        return [{"task_id": task_id or "task-123", "status": "completed"}]


class FakeDLQStore:
    instances: list["FakeDLQStore"] = []

    def __init__(self, redis: FakeRedis, *, prefix: str, ttl_seconds: int | None) -> None:
        self.redis = redis
        self.prefix = prefix
        self.ttl_seconds = ttl_seconds
        self.records: dict[str, DLQRecord] = {}
        self.order: list[str] = []
        FakeDLQStore.instances.append(self)

    async def add(self, record: DLQRecord) -> None:
        self.records[record.dlq_id] = record
        self.order = [item for item in self.order if item != record.dlq_id]
        self.order.insert(0, record.dlq_id)

    async def get(self, dlq_id: str) -> DLQRecord | None:
        return self.records.get(dlq_id)

    async def list_records(
        self,
        *,
        queue_name: str | None = None,
        task_id: str | None = None,
        reason: str | None = None,
        source_queue_name: str | None = None,
        state: DLQRecordState | str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[list[DLQRecord], str | None]:
        normalized_state = DLQRecordState(state) if isinstance(state, str) and state else state
        start = 0
        if cursor and cursor in self.order:
            start = self.order.index(cursor) + 1
        items: list[DLQRecord] = []
        for dlq_id in self.order[start:]:
            record = self.records[dlq_id]
            if queue_name and record.queue_name != queue_name:
                continue
            if task_id and record.task_id != task_id:
                continue
            if reason and record.reason != reason:
                continue
            if source_queue_name and record.source_queue_name != source_queue_name:
                continue
            if normalized_state is not None and record.state != normalized_state:
                continue
            items.append(record)
            if len(items) >= limit:
                break
        next_cursor = items[-1].dlq_id if len(items) == limit else None
        if next_cursor is not None and self.order[-1] == next_cursor:
            next_cursor = None
        return items, next_cursor

    async def summarize_queues(self) -> list[tuple[str, int, object | None]]:
        summary: dict[str, tuple[int, object | None]] = {}
        for dlq_id in self.order:
            record = self.records[dlq_id]
            current_count, current_last = summary.get(record.queue_name, (0, None))
            latest = record.dead_lettered_at if current_last is None or record.dead_lettered_at > current_last else current_last
            summary[record.queue_name] = (current_count + 1, latest)
        return [(queue_name, count, last_indexed_at) for queue_name, (count, last_indexed_at) in summary.items()]

    async def mark_replayed(self, dlq_id: str, *, replayed_at, target_queue_name: str) -> DLQRecord | None:
        record = self.records.get(dlq_id)
        if record is None:
            return None
        updated = record.model_copy(
            update={
                "state": DLQRecordState.REPLAYED,
                "replay_count": record.replay_count + 1,
                "replayed_at": replayed_at,
                "replay_target_queue_name": target_queue_name,
            }
        )
        await self.add(updated)
        return updated


@pytest.fixture(autouse=True)
def patch_fastapi_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeRabbitClient.instances.clear()
    FakeRabbitClient.fail_initialize = False
    FakeRedis.instances.clear()
    FakeStore.instances.clear()
    FakeStore.fail_init = False
    FakeHub.instances.clear()
    FakeSSEStatusStream.instances.clear()
    FakeHistoryReader.instances.clear()
    FakeDLQStore.instances.clear()
    monkeypatch.setattr(relayna_fastapi, "RelaynaRabbitClient", FakeRabbitClient)
    monkeypatch.setattr(relayna_fastapi, "Redis", FakeRedis)
    monkeypatch.setattr(relayna_fastapi, "RedisStatusStore", FakeStore)
    monkeypatch.setattr(relayna_fastapi, "RedisDLQStore", FakeDLQStore)
    monkeypatch.setattr(relayna_fastapi, "StatusHub", FakeHub)
    monkeypatch.setattr(relayna_fastapi, "SSEStatusStream", FakeSSEStatusStream)
    monkeypatch.setattr(relayna_fastapi, "StreamHistoryReader", FakeHistoryReader)


@pytest.fixture
def topology() -> SharedTasksSharedStatusTopology:
    return SharedTasksSharedStatusTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.queue",
        tasks_routing_key="task.request",
        status_exchange="status.exchange",
        status_queue="status.queue",
    )


@pytest.mark.asyncio
async def test_lifespan_startup_and_shutdown_manage_runtime(topology: SharedTasksSharedStatusTopology) -> None:
    app = FastAPI(
        lifespan=relayna_fastapi.create_relayna_lifespan(
            topology=topology,
            redis_url="redis://localhost:6379/0",
        )
    )

    runtime = relayna_fastapi.get_relayna_runtime(app)
    app.include_router(create_status_router(sse_stream=runtime.sse_stream, history_reader=runtime.history_reader))

    assert runtime.hub_task is None

    async with app.router.lifespan_context(app):
        stored_runtime = relayna_fastapi.get_relayna_runtime(app)
        assert stored_runtime is runtime
        assert runtime.rabbitmq.initialize_calls == 1
        assert runtime.hub_task is not None
        await asyncio.sleep(0)
        assert runtime.hub.run_calls == 1

    assert runtime.hub.stop_calls == 1
    assert runtime.rabbitmq.close_calls == 1
    assert runtime.redis.close_calls == 1
    assert not hasattr(app.state, "relayna")
    paths = {route.path for route in app.routes}
    assert "/events/{task_id}" in paths
    assert "/history" in paths


@pytest.mark.asyncio
async def test_get_relayna_runtime_raises_when_missing() -> None:
    app = FastAPI()

    with pytest.raises(RuntimeError, match="Relayna runtime is not available"):
        relayna_fastapi.get_relayna_runtime(app)


@pytest.mark.asyncio
async def test_startup_failure_after_rabbit_init_cleans_up_resources(
    topology: SharedTasksSharedStatusTopology,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_create_task(coro: object, *, name: str | None = None) -> object:
        del name
        coro.close()
        raise RuntimeError("task creation failed")

    monkeypatch.setattr(relayna_fastapi.asyncio, "create_task", fail_create_task)

    app = FastAPI(
        lifespan=relayna_fastapi.create_relayna_lifespan(
            topology=topology,
            redis_url="redis://localhost:6379/0",
        )
    )

    runtime = relayna_fastapi.get_relayna_runtime(app)

    with pytest.raises(RuntimeError, match="task creation failed"):
        async with app.router.lifespan_context(app):
            raise AssertionError("unreachable")

    assert runtime.rabbitmq.initialize_calls == 1
    assert runtime.rabbitmq.close_calls == 1
    assert runtime.redis.close_calls == 1
    assert not hasattr(app.state, "relayna")


@pytest.mark.asyncio
async def test_startup_failure_after_redis_creation_closes_redis(topology: SharedTasksSharedStatusTopology) -> None:
    FakeRabbitClient.fail_initialize = True

    app = FastAPI(
        lifespan=relayna_fastapi.create_relayna_lifespan(
            topology=topology,
            redis_url="redis://localhost:6379/0",
        )
    )

    runtime = relayna_fastapi.get_relayna_runtime(app)

    with pytest.raises(RuntimeError, match="rabbit init failed"):
        async with app.router.lifespan_context(app):
            raise AssertionError("unreachable")

    assert runtime.rabbitmq.close_calls == 1
    assert runtime.redis.close_calls == 1
    assert not hasattr(app.state, "relayna")


@pytest.mark.asyncio
async def test_runtime_is_available_under_custom_state_key(topology: SharedTasksSharedStatusTopology) -> None:
    app = FastAPI(
        lifespan=relayna_fastapi.create_relayna_lifespan(
            topology=topology,
            redis_url="redis://localhost:6379/0",
            app_state_key="custom_runtime",
        )
    )

    runtime = relayna_fastapi.get_relayna_runtime(app, app_state_key="custom_runtime")

    assert runtime.store.prefix == "relayna"
    assert getattr(app.state, "custom_runtime") is runtime


def test_fastapi_testclient_flow_registers_and_serves_relayna_routes(
    topology: SharedTasksSharedStatusTopology,
) -> None:
    app = FastAPI(
        lifespan=relayna_fastapi.create_relayna_lifespan(
            topology=topology,
            redis_url="redis://localhost:6379/0",
        )
    )

    runtime = relayna_fastapi.get_relayna_runtime(app)
    runtime.store.latest_by_task["task-123"] = {"task_id": "task-123", "status": "completed"}
    app.include_router(
        create_status_router(
            sse_stream=runtime.sse_stream,
            history_reader=runtime.history_reader,
            latest_status_store=runtime.store,
        )
    )

    with TestClient(app) as client:
        status_response = client.get("/status/task-123")
        assert status_response.status_code == 200
        assert status_response.json() == {
            "task_id": "task-123",
            "event": {"task_id": "task-123", "status": "completed"},
        }

        history_response = client.get("/history", params={"task_id": "task-123"})
        assert history_response.status_code == 200
        assert history_response.json() == {
            "task_id": "task-123",
            "count": 1,
            "events": [{"task_id": "task-123", "status": "completed"}],
        }

        events_response = client.get("/events/task-123", headers={"Last-Event-ID": "evt-9"})
        assert events_response.status_code == 200
        assert "event: ready" in events_response.text
        assert '"task_id": "task-123"' in events_response.text
        assert getattr(app.state, "relayna") is runtime

    assert runtime.hub.stop_calls == 1
    assert runtime.rabbitmq.close_calls == 1
    assert runtime.redis.close_calls == 1
    assert not hasattr(app.state, "relayna")
    assert FakeHistoryReader.instances[0].replay_calls == [
        {
            "task_id": "task-123",
            "start_offset": "first",
            "max_seconds": None,
            "max_scan": None,
            "require_stream": True,
        }
    ]
    assert FakeSSEStatusStream.instances[0].stream_calls == [
        {"task_id": "task-123", "last_event_id": "evt-9"}
    ]


def test_latest_status_route_returns_404_when_event_missing(topology: SharedTasksSharedStatusTopology) -> None:
    app = FastAPI(
        lifespan=relayna_fastapi.create_relayna_lifespan(
            topology=topology,
            redis_url="redis://localhost:6379/0",
        )
    )

    runtime = relayna_fastapi.get_relayna_runtime(app)
    app.include_router(
        create_status_router(
            sse_stream=runtime.sse_stream,
            latest_status_store=runtime.store,
        )
    )

    with TestClient(app) as client:
        response = client.get("/status/missing-task")
        assert response.status_code == 404
        assert response.json() == {"detail": "No status found for task_id 'missing-task'."}


def test_latest_status_route_applies_output_adapter(topology: SharedTasksSharedStatusTopology) -> None:
    app = FastAPI(
        lifespan=relayna_fastapi.create_relayna_lifespan(
            topology=topology,
            redis_url="redis://localhost:6379/0",
        )
    )

    runtime = relayna_fastapi.get_relayna_runtime(app)
    runtime.store.latest_by_task["task-123"] = {"task_id": "task-123", "status": "completed"}
    app.include_router(
        create_status_router(
            sse_stream=runtime.sse_stream,
            latest_status_store=runtime.store,
            latest_output_adapter=lambda event: {**event, "documentId": event["task_id"]},
        )
    )

    with TestClient(app) as client:
        response = client.get("/status/task-123")
        assert response.status_code == 200
        assert response.json() == {
            "task_id": "task-123",
            "event": {"task_id": "task-123", "status": "completed", "documentId": "task-123"},
        }


def test_status_route_is_not_registered_when_store_is_missing(topology: SharedTasksSharedStatusTopology) -> None:
    app = FastAPI(
        lifespan=relayna_fastapi.create_relayna_lifespan(
            topology=topology,
            redis_url="redis://localhost:6379/0",
        )
    )

    runtime = relayna_fastapi.get_relayna_runtime(app)
    app.include_router(create_status_router(sse_stream=runtime.sse_stream, history_reader=runtime.history_reader))

    paths = {route.path for route in app.routes}

    assert "/status/{task_id}" not in paths


def test_runtime_can_optionally_create_dlq_store(topology: SharedTasksSharedStatusTopology) -> None:
    app = FastAPI(
        lifespan=relayna_fastapi.create_relayna_lifespan(
            topology=topology,
            redis_url="redis://localhost:6379/0",
            dlq_store_prefix="relayna-dlq",
        )
    )

    runtime = relayna_fastapi.get_relayna_runtime(app)

    assert runtime.dlq_store is not None
    assert runtime.dlq_store.prefix == "relayna-dlq"


def test_dlq_router_lists_details_and_replays_messages(topology: SharedTasksSharedStatusTopology) -> None:
    app = FastAPI(
        lifespan=relayna_fastapi.create_relayna_lifespan(
            topology=topology,
            redis_url="redis://localhost:6379/0",
            dlq_store_prefix="relayna-dlq",
        )
    )

    runtime = relayna_fastapi.get_relayna_runtime(app)
    assert runtime.dlq_store is not None
    runtime.store.latest_by_task["task-123"] = {"task_id": "task-123", "status": "failed"}
    runtime.store.history_by_task["task-123"] = [
        {"task_id": "task-123", "status": "failed"},
        {"task_id": "task-123", "status": "retrying"},
    ]

    record = build_dlq_record(
        queue_name="tasks.queue.dlq",
        source_queue_name="tasks.queue",
        retry_queue_name="tasks.queue.retry",
        task_id="task-123",
        correlation_id="corr-123",
        reason="handler_error",
        exception_type="RuntimeError",
        retry_attempt=2,
        max_retries=2,
        headers={"x-relayna-retry-attempt": 2},
        content_type="application/json",
        body=b'{"task_id":"task-123","payload":{"kind":"demo"}}',
    )
    runtime.rabbitmq.queue_counts["tasks.queue.dlq"] = 1

    async def preload() -> None:
        await runtime.dlq_store.add(record)

    asyncio.run(preload())

    app.include_router(
        create_dlq_router(
            dlq_service=DLQService(
                rabbitmq=runtime.rabbitmq,
                dlq_store=runtime.dlq_store,
                status_store=runtime.store,
            )
        )
    )

    with TestClient(app) as client:
        queues_response = client.get("/dlq/queues")
        assert queues_response.status_code == 200
        assert queues_response.json() == {
            "queues": [
                {
                    "queue_name": "tasks.queue.dlq",
                    "indexed_count": 1,
                    "last_indexed_at": record.dead_lettered_at.isoformat().replace("+00:00", "Z"),
                    "exists": True,
                    "message_count": 1,
                }
            ]
        }

        messages_response = client.get("/dlq/messages", params={"task_id": "task-123"})
        assert messages_response.status_code == 200
        assert messages_response.json()["items"][0]["dlq_id"] == record.dlq_id

        detail_response = client.get(f"/dlq/messages/{record.dlq_id}")
        assert detail_response.status_code == 200
        assert detail_response.json()["latest_status"] == {"task_id": "task-123", "status": "failed"}
        assert detail_response.json()["status_history"] == [
            {"task_id": "task-123", "status": "failed"},
            {"task_id": "task-123", "status": "retrying"},
        ]

        replay_response = client.post(f"/dlq/messages/{record.dlq_id}/replay")
        assert replay_response.status_code == 200
        assert replay_response.json()["target_queue_name"] == "tasks.queue.retry"
        assert runtime.rabbitmq.raw_queue_publishes[0]["queue_name"] == "tasks.queue.retry"
        assert runtime.rabbitmq.raw_queue_publishes[0]["headers"]["x-relayna-replayed-from-dlq"] is True

        conflict_response = client.post(f"/dlq/messages/{record.dlq_id}/replay")
        assert conflict_response.status_code == 409


def test_dlq_router_returns_404_for_unknown_message(topology: SharedTasksSharedStatusTopology) -> None:
    app = FastAPI(
        lifespan=relayna_fastapi.create_relayna_lifespan(
            topology=topology,
            redis_url="redis://localhost:6379/0",
            dlq_store_prefix="relayna-dlq",
        )
    )

    runtime = relayna_fastapi.get_relayna_runtime(app)
    assert runtime.dlq_store is not None
    app.include_router(
        create_dlq_router(
            dlq_service=DLQService(
                rabbitmq=runtime.rabbitmq,
                dlq_store=runtime.dlq_store,
                status_store=runtime.store,
            )
        )
    )

    with TestClient(app) as client:
        response = client.get("/dlq/messages/missing")
        assert response.status_code == 404
        assert response.json() == {"detail": "No DLQ message found for dlq_id 'missing'."}
        replay_response = client.post("/dlq/messages/missing/replay")
        assert replay_response.status_code == 404
        assert replay_response.json() == {"detail": "No DLQ message found for dlq_id 'missing'."}
