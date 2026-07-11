from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from relayna.dlq import (
    DLQRecordState,
    DLQReplayConflict,
    DLQService,
    FailedTaskInvestigationStatus,
    RedisDLQStore,
    build_dlq_record,
)
from relayna.dlq.broker import broker_message_from_management_payload
from relayna.dlq.service import _retry_payload_bytes, _task_id_from_payload
from relayna.dlq.store import _failed_record_matches, _timestamp


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

    def zadd(self, key: str, mapping: dict[str, float]) -> None:
        self._ops.append(("zadd", (key, mapping), {}))

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
            elif op == "zadd":
                key, mapping = args
                self._redis.sorted_sets.setdefault(str(key), {}).update(mapping)  # type: ignore[arg-type]
                results.append(1)
        return results


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}
        self.sorted_sets: dict[str, dict[str, float]] = {}
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

    async def mget(self, keys: list[str]) -> list[str | None]:
        return [self.values.get(key) for key in keys]

    async def lrange(self, key: str, start: int, stop: int) -> list[str]:
        values = self.lists.get(key, [])
        if stop == -1:
            return values[start:]
        return values[start : stop + 1]

    async def lpos(self, key: str, value: str) -> int | None:
        try:
            return self.lists.get(key, []).index(value)
        except ValueError:
            return None

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        self.sorted_sets.setdefault(key, {}).update(mapping)
        return len(mapping)

    async def zrem(self, key: str, *values: str) -> int:
        stored = self.sorted_sets.setdefault(key, {})
        removed = 0
        for value in values:
            if value in stored:
                removed += 1
                stored.pop(value, None)
        return removed

    async def zrevrangebyscore(
        self,
        key: str,
        max_score: object,
        min_score: object,
        *,
        start: int = 0,
        num: int | None = None,
    ) -> list[str]:
        stored = self.sorted_sets.get(key, {})
        high = float("inf") if max_score == "+inf" else float(max_score)
        low = float("-inf") if min_score == "-inf" else float(min_score)
        values = [
            member
            for member, score in sorted(stored.items(), key=lambda item: item[1], reverse=True)
            if low <= score <= high
        ]
        return values[start:] if num is None else values[start : start + num]

    async def zremrangebyscore(self, key: str, min_score: object, max_score: object) -> int:
        stored = self.sorted_sets.setdefault(key, {})
        low = float("-inf") if min_score == "-inf" else float(min_score)
        high = float("inf") if max_score == "+inf" else float(max_score)
        to_remove = [member for member, score in stored.items() if low <= score <= high]
        for member in to_remove:
            stored.pop(member, None)
        return len(to_remove)


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


def test_dlq_record_builds_structured_diagnosis() -> None:
    record = make_record()

    assert record.diagnosis is not None
    assert record.diagnosis.failure.reason == "handler_error"
    assert record.diagnosis.failure.exception_type == "RuntimeError"
    assert record.diagnosis.failure.terminal_retry_attempt == 2
    assert record.diagnosis.retry.source_queue_name == "tasks.queue"
    assert record.diagnosis.retry.retry_queue_name == "tasks.queue.retry"
    assert record.diagnosis.ownership.task_id == "task-123"
    assert record.diagnosis.envelope.body_encoding == "json"
    assert "Message reached the configured retry limit." in record.diagnosis.replay.warnings


def test_dlq_record_still_validates_without_diagnosis() -> None:
    record = make_record()
    payload = record.model_dump(mode="json")
    payload.pop("diagnosis", None)

    restored = type(record).model_validate(payload)

    assert restored.diagnosis is None
    assert restored.dlq_id == record.dlq_id


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
    assert updated.diagnosis == record_b.diagnosis

    replayed_only, _ = await store.list_records(state=DLQRecordState.REPLAYED, limit=10)
    assert [record.dlq_id for record in replayed_only] == [record_b.dlq_id]

    queue_summary = await store.summarize_queues()
    assert ("tasks.queue.dlq", 1, datetime(2026, 3, 21, 1, tzinfo=UTC)) in queue_summary
    assert ("aggregation.queue.0.dlq", 1, datetime(2026, 3, 21, 2, tzinfo=UTC)) in queue_summary
    assert record_b.dlq_id in redis.sorted_sets[store.failed_tasks_index_key()]


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
    assert detail.diagnosis == record.diagnosis
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


@pytest.mark.asyncio
async def test_failed_task_registry_lists_detail_investigation_retry_and_delete() -> None:
    redis = FakeRedis()
    store = RedisDLQStore(redis, prefix="relayna-dlq", ttl_seconds=604800)
    rabbit = FakeRabbit()
    status_store = FakeStatusStore()
    status_store.history["task-123"] = [{"timestamp": "2026-03-21T00:00:00Z", "message": "failed"}]
    service = DLQService(rabbitmq=rabbit, dlq_store=store, status_store=status_store)
    record = make_record(dead_lettered_at=datetime(2026, 3, 21, 1, tzinfo=UTC))
    await store.add(record)

    listed = await service.list_failed_tasks(investigation_status="unreviewed", limit=10)

    assert listed.next_cursor is None
    assert listed.items[0].failure_id == record.dlq_id
    assert listed.items[0].dlq_name == "tasks.queue.dlq"
    assert listed.items[0].error_type == "RuntimeError"

    detail = await service.get_failed_task_detail(record.dlq_id)
    assert detail is not None
    assert detail.last_logs == [{"timestamp": "2026-03-21T00:00:00Z", "message": "failed"}]
    assert detail.raw_body_b64 == record.raw_body_b64

    investigated = await service.mark_failed_task_investigated(
        record.dlq_id, investigated_by="admin@example.test", note="known issue"
    )
    assert investigated is not None
    assert investigated.investigation_status == "investigated"
    assert investigated.investigated_by == "admin@example.test"
    assert investigated.investigation_note == "known issue"

    retry_result = await service.retry_failed_task(record.dlq_id)
    assert retry_result is not None
    assert retry_result.target_queue == "tasks.queue"
    assert rabbit.publishes[0]["queue_name"] == "tasks.queue"
    assert rabbit.publishes[0]["headers"]["x-relayna-manual-retry-from-failure-id"] == record.dlq_id
    stored = await store.get(record.dlq_id)
    assert stored is not None
    assert stored.retry_status == "retried"

    uninvestigated = await service.mark_failed_task_uninvestigated(record.dlq_id)
    assert uninvestigated is not None
    assert uninvestigated.investigation_status == "unreviewed"

    assert await service.delete_failed_task(record.dlq_id) is True
    assert await service.get_failed_task_detail(record.dlq_id) is None


@pytest.mark.asyncio
async def test_redis_dlq_store_pagination_filters_conflicts_and_cleanup_paths() -> None:
    redis = FakeRedis()
    store = RedisDLQStore(redis, prefix="coverage", ttl_seconds=60)
    base = make_record(dead_lettered_at=datetime(2026, 1, 1, tzinfo=UTC))
    first = base.model_copy(
        update={
            "dlq_id": "one",
            "service_name": "service-a",
            "worker_id": "worker-a",
            "status": "failed",
            "investigation_status": FailedTaskInvestigationStatus.UNREVIEWED,
        }
    )
    second = base.model_copy(
        update={
            "dlq_id": "two",
            "task_id": "task-2",
            "queue_name": "other.dlq",
            "source_queue_name": "other",
            "reason": "invalid",
            "state": DLQRecordState.REPLAYED,
            "dead_lettered_at": datetime(2026, 1, 2, tzinfo=UTC),
        }
    )
    third = base.model_copy(
        update={
            "dlq_id": "three",
            "task_id": "task-3",
            "dead_lettered_at": datetime(2026, 1, 3, tzinfo=UTC),
        }
    )
    for record in (first, second, third):
        await store.add(record)
    assert await store._get_many([]) == []
    redis.lists[store.records_key()].insert(0, "missing")

    page, cursor = await store.list_records(limit=1)
    assert len(page) == 1 and cursor == page[0].dlq_id
    next_page, _ = await store.list_records(cursor=cursor, limit=5)
    assert all(record.dlq_id != cursor for record in next_page)
    fallback, _ = await store.list_records(cursor="unknown", limit=1)
    assert fallback
    assert (await store.list_records(queue_name="absent"))[0] == []
    assert (await store.list_records(task_id="task-2"))[0] == [second]
    assert (await store.list_records(reason="invalid"))[0] == [second]
    assert (await store.list_records(source_queue_name="other"))[0] == [second]
    assert (await store.list_records(state="replayed"))[0] == [second]

    failed_page, failed_cursor = await store.list_failed_task_records(limit=1)
    assert len(failed_page) == 1 and failed_cursor == failed_page[0].dlq_id
    after, _ = await store.list_failed_task_records(cursor=failed_cursor, limit=5)
    assert all(record.dlq_id != failed_cursor for record in after)
    invalid_cursor, _ = await store.list_failed_task_records(cursor="unknown", limit=1)
    assert invalid_cursor
    filtered, _ = await store.list_failed_task_records(
        service_name="service-a",
        queue_name="tasks.queue",
        dlq_name="tasks.queue.dlq",
        error_type="RuntimeError",
        status="failed",
        task_id="task-123",
        worker_id="worker-a",
        investigation_status=FailedTaskInvestigationStatus.UNREVIEWED,
        failed_from=datetime(2025, 1, 1, tzinfo=UTC),
        failed_to=datetime(2027, 1, 1, tzinfo=UTC),
    )
    assert filtered == [first]

    assert await store.claim_replay("missing-record") is None
    redis.values[store.replay_lock_key("locked")] = "1"
    with pytest.raises(DLQReplayConflict, match="already in progress"):
        await store.claim_replay("locked")
    with pytest.raises(DLQReplayConflict):
        await store.claim_replay("two")
    assert await store.claim_replay("two", force=True) == second
    assert await store.mark_replayed("missing-record", replayed_at=datetime.now(UTC), target_queue_name="queue") is None
    await store.release_replay_claim("two")

    no_ttl = RedisDLQStore(redis, prefix="no-ttl", ttl_seconds=None)
    assert await no_ttl.cleanup_failed_task_index() == 0
    assert await store.cleanup_failed_task_index(older_than=datetime(2030, 1, 1, tzinfo=UTC)) >= 1
    assert _timestamp(datetime(2026, 1, 1)) == _timestamp(datetime(2026, 1, 1, tzinfo=UTC))


def test_failed_record_matcher_rejects_every_filter_dimension() -> None:
    record = make_record().model_copy(
        update={
            "service_name": "service",
            "worker_id": "worker",
            "status": "failed",
            "investigation_status": FailedTaskInvestigationStatus.UNREVIEWED,
        }
    )
    matching = {
        "service_name": "service",
        "queue_name": "tasks.queue",
        "dlq_name": "tasks.queue.dlq",
        "error_type": "RuntimeError",
        "status": "failed",
        "task_id": "task-123",
        "worker_id": "worker",
        "investigation_status": FailedTaskInvestigationStatus.UNREVIEWED,
    }
    assert _failed_record_matches(record, **matching) is True
    for key in matching:
        changed = dict(matching)
        changed[key] = FailedTaskInvestigationStatus.INVESTIGATED if key == "investigation_status" else "wrong"
        assert _failed_record_matches(record, **changed) is False


@pytest.mark.asyncio
async def test_failed_task_retry_rejects_non_terminal_status() -> None:
    redis = FakeRedis()
    store = RedisDLQStore(redis, prefix="relayna-dlq")
    service = DLQService(rabbitmq=FakeRabbit(), dlq_store=store)
    record = make_record().model_copy(update={"status": "retrying"})
    await store.add(record)

    with pytest.raises(Exception, match="Manual retry is only allowed"):
        await service.retry_failed_task(record.dlq_id)


@pytest.mark.asyncio
async def test_dlq_service_broker_replay_missing_update_retry_payload_and_summary_skip_paths() -> None:
    redis = FakeRedis()
    store = RedisDLQStore(redis, prefix="tail")
    rabbit = FakeRabbit()
    service = DLQService(rabbitmq=rabbit, dlq_store=store)
    with pytest.raises(RuntimeError, match="not configured"):
        await service.list_broker_messages(["queue"])

    class Inspector:
        async def list_messages(self, queue_name: str, *, limit: int = 50):
            return [
                broker_message_from_management_payload(
                    queue_name,
                    {"payload": '{"task_id":"task"}'},
                ),
                broker_message_from_management_payload(
                    queue_name,
                    {"payload": '{"task_id":"other"}'},
                ),
            ]

    broker_service = DLQService(rabbitmq=rabbit, dlq_store=store, broker_message_inspector=Inspector())  # type: ignore[arg-type]
    assert broker_service.supports_broker_message_reads is True
    with pytest.raises(ValueError, match="Unsupported broker"):
        await broker_service.list_broker_messages(["queue"], queue_name="missing")
    broker_items = await broker_service.list_broker_messages(
        ["", "queue", "queue"],
        task_id="task",
        limit=500,
    )
    assert len(broker_items.items) == 1

    class MissingClaimStore:
        async def claim_replay(self, dlq_id: str, *, force: bool = False):
            return None

        async def release_replay_claim(self, dlq_id: str) -> None:
            self.released = dlq_id

    missing_store = MissingClaimStore()
    missing_service = DLQService(rabbitmq=rabbit, dlq_store=missing_store)  # type: ignore[arg-type]
    assert await missing_service.replay_message("missing") is None
    assert missing_store.released == "missing"

    record = make_record().model_copy(update={"status": "DLQ", "payload_available": False, "raw_body_b64": ""})
    await store.add(record)
    with pytest.raises(Exception, match="payload is unavailable"):
        await service.retry_failed_task(record.dlq_id)
    assert await service.retry_failed_task("missing") is None
    assert await service.mark_failed_task_investigated("missing") is None
    assert await service.mark_failed_task_uninvestigated("missing") is None

    valid = make_record()
    assert _retry_payload_bytes(valid, "text") == b"text"
    assert _retry_payload_bytes(valid, {"task_id": 7}).startswith(b"{")
    assert _task_id_from_payload({"task_id": 7}) == "7"
    assert _task_id_from_payload([]) is None

    rabbit.queue_counts["exists.dlq"] = 0
    summaries = await service._build_queue_summaries(
        [("missing.dlq", 0, None), ("exists.dlq", 0, None)],
        include_broker_only=True,
    )
    assert [item.queue_name for item in summaries] == ["exists.dlq"]
