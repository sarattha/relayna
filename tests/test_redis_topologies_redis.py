from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from redis.asyncio import Redis, RedisCluster

from relayna.dlq import RedisDLQStore, build_dlq_record
from relayna.observability import RedisObservationStore, RedisServiceEventFeedStore, SSEKeepaliveSent
from relayna.status import RedisStatusStore, SSEStatusStream
from relayna.storage import RedisTaskLeaseStore, RedisWorkflowContractStore, TaskLease

pytestmark = pytest.mark.skipif(
    "RELAYNA_TEST_REDIS_URL" not in os.environ,
    reason="Set RELAYNA_TEST_REDIS_URL to run real Redis topology tests.",
)


def _client(url: str, mode: str | None = None) -> Any:
    selected_mode = mode or os.environ.get("RELAYNA_TEST_REDIS_MODE", "standalone")
    client_type = RedisCluster if selected_mode == "cluster" else Redis
    return client_type.from_url(url, protocol=3, decode_responses=True, max_connections=256)


async def _delete_test_keys(redis: Any, prefix: str) -> None:
    async for key in redis.scan_iter(match=f"*:{prefix}:*"):
        await redis.delete(key)


@pytest.mark.asyncio
async def test_relayna_stores_and_sse_against_real_redis_topology() -> None:
    redis = _client(os.environ["RELAYNA_TEST_REDIS_URL"])
    await redis.initialize()
    prefix = f"relayna-topology-test-{uuid4().hex}"
    feed = RedisServiceEventFeedStore(redis, prefix=f"{prefix}:feed", ttl_seconds=60, feed_maxlen=20)
    status = RedisStatusStore(
        redis,
        prefix=f"{prefix}:status",
        ttl_seconds=60,
        history_maxlen=10,
        service_event_store=feed,
    )
    observations = RedisObservationStore(
        redis,
        prefix=f"{prefix}:observations",
        ttl_seconds=60,
        history_maxlen=10,
        service_event_store=feed,
    )
    dlq = RedisDLQStore(redis, prefix=f"{prefix}:dlq", ttl_seconds=60)
    leases = RedisTaskLeaseStore(redis, prefix=f"{prefix}:leases")
    contracts = RedisWorkflowContractStore(redis, prefix=f"{prefix}:contracts", ttl_seconds=60)

    task_id = "task-1"
    event = {
        "task_id": task_id,
        "status": "completed",
        "event_id": "event-1",
        "meta": {"parent_task_id": "parent-1"},
    }
    stream = SSEStatusStream(store=status, keepalive_interval_seconds=None)
    iterator = stream.stream(task_id)
    try:
        if isinstance(redis, RedisCluster):
            assert redis.connection_kwargs["protocol"] == 3
        else:
            hello = await redis.execute_command("HELLO", 3)
            assert int(hello["proto"]) == 3
        assert (await anext(iterator)).startswith(b"event: ready")
        live_event = asyncio.create_task(anext(iterator))
        await asyncio.sleep(0.1)
        await status.set_history(task_id, event)
        chunk = await asyncio.wait_for(live_event, timeout=5)
        assert b'"status": "completed"' in chunk
        assert await status.get_latest(task_id) == event
        assert await status.get_child_task_ids("parent-1") == [task_id]

        assert await observations.set_event(SSEKeepaliveSent(task_id=task_id)) is True
        assert (await observations.get_history(task_id))[0]["event_type"] == "SSEKeepaliveSent"
        assert {item.source_kind for item in (await feed.get_feed(limit=10)).items} == {
            "status",
            "observation",
        }

        record = build_dlq_record(
            queue_name="tasks.dlq",
            source_queue_name="tasks",
            retry_queue_name="tasks.retry",
            task_id=task_id,
            correlation_id=task_id,
            reason="handler_error",
            exception_type="RuntimeError",
            retry_attempt=1,
            max_retries=1,
            headers={},
            content_type="application/json",
            body=b'{"task_id":"task-1"}',
            dead_lettered_at=datetime.now(UTC),
        )
        await dlq.add(record)
        records, cursor = await dlq.list_records(limit=10)
        assert records == [record]
        assert cursor is None

        lease = TaskLease(
            lease_id="lease-1",
            task_id=task_id,
            owner_id="worker-1",
            consumer_name="worker",
            expires_at=datetime.now(UTC) + timedelta(seconds=60),
        )
        assert await leases.acquire(lease) is True
        assert await leases.list_by_owner("worker-1") == [lease]
        assert await leases.release(lease.lease_id, owner_id=lease.owner_id) is True

        contract_args = {
            "stage": "planner",
            "task_id": task_id,
            "action": "plan",
            "payload": {"request_id": "request-1"},
            "dedup_key_fields": ("request_id",),
        }
        assert await contracts.acquire_dedup(**contract_args) is True
        assert await contracts.acquire_dedup(**contract_args) is False
        await contracts.mark_inflight(**contract_args)
        await contracts.clear_inflight(**contract_args)

        replica_urls = [url for url in os.environ.get("RELAYNA_TEST_REDIS_REPLICA_URLS", "").split(",") if url]
        if replica_urls:
            assert await redis.wait(len(replica_urls), 5000) == len(replica_urls)
            for replica_url in replica_urls:
                replica = _client(replica_url, "standalone")
                try:
                    await replica.initialize()
                    assert await replica.lindex(status.history_key(task_id), 0) is not None
                finally:
                    await replica.aclose()
    finally:
        await iterator.aclose()
        await _delete_test_keys(redis, prefix)
        await redis.aclose()
