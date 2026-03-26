from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from relayna.dlq import (
    DLQRecordState,
    DLQReplayConflict,
    DLQService,
    RedisDLQStore,
    build_dlq_record,
)


class FakePipeline:
    def __init__(self, redis: FakeRedis) -> None:
        self._redis = redis
        self._ops: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._ops.append(("set", (key, value), {"ex": ex}))

    def lpush(self, key: str, value: str) -> None:
        self._ops.append(("lpush", (key, value), {}))

    def expire(self, key: str, ttl: int) -> None:
        self._ops.append(("expire", (key, ttl), {}))

    async def execute(self) -> list[object]:
        results: list[object] = []
        for op, args, kwargs in self._ops:
            if op == "set":
                key, value = args
                self._redis.values[str(key)] = str(value)
                if kwargs["ex"] is not None:
                    self._redis.expirations[str(key)] = int(kwargs["ex"])
                results.append(True)
            elif op == "lpush":
                key, value = args
                self._redis.lists.setdefault(str(key), []).insert(0, str(value))
                results.append(1)
            elif op == "expire":
                key, ttl = args
                self._redis.expirations[str(key)] = int(ttl)
                results.append(True)
        return results


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}
        self.expirations: dict[str, int] = {}

    def pipeline(self) -> FakePipeline:
        return FakePipeline(self)

    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False) -> bool:
        if nx and key in self.values:
            return False
        self.values[key] = value
        if ex is not None:
            self.expirations[key] = ex
        return True

    async def delete(self, key: str) -> int:
        existed = key in self.values
        self.values.pop(key, None)
        return 1 if existed else 0

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def lrange(self, key: str, start: int, stop: int) -> list[str]:
        values = self.lists.get(key, [])
        if stop == -1:
            return values[start:]
        return values[start : stop + 1]


class FakeRabbit:
    def __init__(self) -> None:
        self.queue_counts: dict[str, int] = {}
        self.publishes: list[dict[str, Any]] = []

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
        headers: dict[str, Any] | None = None,
        content_type: str | None = None,
        delivery_mode: object | None = None,
    ) -> None:
        self.publishes.append(
            {
                "queue_name": queue_name,
                "body": body,
                "correlation_id": correlation_id,
                "headers": dict(headers or {}),
                "content_type": content_type,
                "delivery_mode": delivery_mode,
            }
        )


class FakeStatusStore:
    def __init__(self) -> None:
        self.latest: dict[str, dict[str, Any]] = {}
        self.history: dict[str, list[dict[str, Any]]] = {}

    async def get_latest(self, task_id: str) -> dict[str, Any] | None:
        return self.latest.get(task_id)

    async def get_history(self, task_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        items = self.history.get(task_id, [])
        if limit is None:
            return list(items)
        return list(items[:limit])


def make_record(
    *,
    queue_name: str = "tasks.queue.dlq",
    source_queue_name: str = "tasks.queue",
    retry_queue_name: str = "tasks.queue.retry",
    task_id: str | None = "task-123",
    reason: str = "handler_error",
    retry_attempt: int = 2,
    dead_lettered_at: datetime | None = None,
) -> object:
    return build_dlq_record(
        queue_name=queue_name,
        source_queue_name=source_queue_name,
        retry_queue_name=retry_queue_name,
        task_id=task_id,
        correlation_id=task_id,
        reason=reason,
        exception_type="RuntimeError",
        retry_attempt=retry_attempt,
        max_retries=2,
        headers={
            "x-relayna-retry-attempt": retry_attempt,
            "x-relayna-max-retries": 2,
            "x-relayna-source-queue": source_queue_name,
            "x-relayna-failure-reason": reason,
            "x-relayna-exception-type": "RuntimeError",
        },
        content_type="application/json",
        body=b'{"task_id":"task-123","payload":{"kind":"demo"}}',
        dead_lettered_at=dead_lettered_at or datetime(2026, 3, 21, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_redis_dlq_store_lists_filters_and_marks_replayed() -> None:
    redis = FakeRedis()
    store = RedisDLQStore(redis, prefix="relayna-dlq", ttl_seconds=300)
    record_a = make_record(dead_lettered_at=datetime(2026, 3, 21, 1, tzinfo=UTC))
    record_b = make_record(
        queue_name="aggregation.queue.0.dlq",
        source_queue_name="aggregation.queue.0",
        retry_queue_name="aggregation.queue.0.retry",
        task_id="task-456",
        reason="invalid_envelope",
        retry_attempt=0,
        dead_lettered_at=datetime(2026, 3, 21, 2, tzinfo=UTC),
    )

    await store.add(record_a)
    await store.add(record_b)

    filtered, next_cursor = await store.list_records(task_id="task-456", reason="invalid_envelope", limit=10)
    assert next_cursor is None
    assert [record.dlq_id for record in filtered] == [record_b.dlq_id]

    updated = await store.mark_replayed(
        record_b.dlq_id,
        replayed_at=datetime(2026, 3, 21, 3, tzinfo=UTC),
        target_queue_name="aggregation.queue.0.retry",
    )
    assert updated is not None
    assert updated.state == DLQRecordState.REPLAYED
    assert updated.replay_count == 1

    replayed_only, _ = await store.list_records(state=DLQRecordState.REPLAYED, limit=10)
    assert [record.dlq_id for record in replayed_only] == [record_b.dlq_id]

    queue_summary = await store.summarize_queues()
    assert ("tasks.queue.dlq", 1, datetime(2026, 3, 21, 1, tzinfo=UTC)) in queue_summary
    assert ("aggregation.queue.0.dlq", 1, datetime(2026, 3, 21, 2, tzinfo=UTC)) in queue_summary


@pytest.mark.asyncio
async def test_dlq_service_replay_resets_retry_headers_and_marks_record() -> None:
    redis = FakeRedis()
    store = RedisDLQStore(redis, prefix="relayna-dlq")
    rabbit = FakeRabbit()
    status_store = FakeStatusStore()
    service = DLQService(rabbitmq=rabbit, dlq_store=store, status_store=status_store)
    record = make_record()
    await store.add(record)

    result = await service.replay_message(record.dlq_id)

    assert result is not None
    assert result.target_queue_name == "tasks.queue.retry"
    assert rabbit.publishes[0]["queue_name"] == "tasks.queue.retry"
    assert rabbit.publishes[0]["body"] == b'{"task_id":"task-123","payload":{"kind":"demo"}}'
    assert rabbit.publishes[0]["headers"]["x-relayna-retry-attempt"] == 0
    assert "x-relayna-failure-reason" not in rabbit.publishes[0]["headers"]
    assert "x-relayna-exception-type" not in rabbit.publishes[0]["headers"]
    assert rabbit.publishes[0]["headers"]["x-relayna-original-retry-attempt"] == 2
    assert rabbit.publishes[0]["headers"]["x-relayna-original-failure-reason"] == "handler_error"
    assert rabbit.publishes[0]["headers"]["x-relayna-original-exception-type"] == "RuntimeError"
    assert rabbit.publishes[0]["headers"]["x-relayna-replayed-from-dlq"] is True
    assert rabbit.publishes[0]["headers"]["x-relayna-dlq-id"] == record.dlq_id

    stored = await store.get(record.dlq_id)
    assert stored is not None
    assert stored.state == DLQRecordState.REPLAYED
    assert stored.replay_count == 1
    assert stored.replay_target_queue_name == "tasks.queue.retry"

    with pytest.raises(DLQReplayConflict):
        await service.replay_message(record.dlq_id)


@pytest.mark.asyncio
async def test_dlq_service_detail_and_queue_summary_use_status_store_and_rabbitmq() -> None:
    redis = FakeRedis()
    store = RedisDLQStore(redis, prefix="relayna-dlq")
    rabbit = FakeRabbit()
    rabbit.queue_counts["tasks.queue.dlq"] = 3
    status_store = FakeStatusStore()
    status_store.latest["task-123"] = {"task_id": "task-123", "status": "failed"}
    status_store.history["task-123"] = [
        {"task_id": "task-123", "status": "failed"},
        {"task_id": "task-123", "status": "retrying"},
    ]
    service = DLQService(rabbitmq=rabbit, dlq_store=store, status_store=status_store)
    record = make_record()
    await store.add(record)

    detail = await service.get_message_detail(record.dlq_id)
    queue_summaries = await service.get_queue_summaries()

    assert detail is not None
    assert detail.latest_status == {"task_id": "task-123", "status": "failed"}
    assert detail.status_history == [
        {"task_id": "task-123", "status": "failed"},
        {"task_id": "task-123", "status": "retrying"},
    ]
    assert queue_summaries[0].queue_name == "tasks.queue.dlq"
    assert queue_summaries[0].indexed_count == 1
    assert queue_summaries[0].exists is True
    assert queue_summaries[0].message_count == 3


@pytest.mark.asyncio
async def test_redis_dlq_store_summarize_queues_scans_full_index_without_list_records() -> None:
    redis = FakeRedis()
    store = RedisDLQStore(redis, prefix="relayna-dlq")
    record_a = make_record(dead_lettered_at=datetime(2026, 3, 21, 1, tzinfo=UTC))
    record_b = make_record(
        queue_name="aggregation.queue.0.dlq",
        source_queue_name="aggregation.queue.0",
        retry_queue_name="aggregation.queue.0.retry",
        task_id="task-456",
        dead_lettered_at=datetime(2026, 3, 21, 2, tzinfo=UTC),
    )
    await store.add(record_a)
    await store.add(record_b)

    async def fail_list_records(**kwargs):
        raise AssertionError(f"unexpected list_records call: {kwargs}")

    store.list_records = fail_list_records  # type: ignore[method-assign]

    queue_summary = await store.summarize_queues()

    assert ("tasks.queue.dlq", 1, datetime(2026, 3, 21, 1, tzinfo=UTC)) in queue_summary
    assert ("aggregation.queue.0.dlq", 1, datetime(2026, 3, 21, 2, tzinfo=UTC)) in queue_summary


@pytest.mark.asyncio
async def test_dlq_service_replay_conflicts_when_claim_already_exists() -> None:
    redis = FakeRedis()
    store = RedisDLQStore(redis, prefix="relayna-dlq")
    rabbit = FakeRabbit()
    service = DLQService(rabbitmq=rabbit, dlq_store=store)
    record = make_record()
    await store.add(record)
    redis.values[store.replay_lock_key(record.dlq_id)] = "1"

    with pytest.raises(DLQReplayConflict, match="already in progress"):
        await service.replay_message(record.dlq_id)

    assert rabbit.publishes == []
