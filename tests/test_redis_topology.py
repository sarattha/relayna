from __future__ import annotations

from typing import Any

from redis.cluster import key_slot

from relayna._redis import redis_key
from relayna.dlq import RedisDLQStore
from relayna.observability import RedisObservationStore, RedisServiceEventFeedStore
from relayna.status import RedisStatusStore
from relayna.storage import RedisTaskLeaseStore, RedisWorkflowContractStore
from relayna.storage.redis_models import fanin_key, run_state_key


def _slot(key: str) -> int:
    return key_slot(key.encode())


def test_redis_key_places_its_generated_hash_tag_before_untrusted_prefix_braces() -> None:
    key = redis_key("tenant:{outside}", "status:task-1", "history", "task-1")

    assert key.startswith("{relayna:")
    assert key.endswith(":tenant:{outside}:history:task-1")


def test_status_and_observation_atomic_keys_share_a_per_task_slot() -> None:
    redis = object()
    status = RedisStatusStore(redis, prefix="status")  # type: ignore[arg-type]
    observation = RedisObservationStore(redis, prefix="observations")  # type: ignore[arg-type]
    event: dict[str, Any] = {"task_id": "task-1", "event_id": "event-1"}

    assert (
        len(
            {
                _slot(status.history_key("task-1")),
                _slot(status.channel_name("task-1")),
                _slot(status.event_key("task-1", event)),
            }
        )
        == 1
    )
    assert (
        len(
            {
                _slot(observation.history_key("task-1")),
                _slot(observation.event_key("task-1", event)),
            }
        )
        == 1
    )
    assert _slot(status.history_key("task-1")) != _slot(status.history_key("task-2"))


def test_global_multi_key_stores_keep_all_related_keys_in_one_slot() -> None:
    redis = object()
    feed = RedisServiceEventFeedStore(redis, prefix="feed")  # type: ignore[arg-type]
    dlq = RedisDLQStore(redis, prefix="dlq")  # type: ignore[arg-type]
    leases = RedisTaskLeaseStore(redis, prefix="leases")  # type: ignore[arg-type]

    assert (
        len(
            {
                _slot(feed.feed_key()),
                _slot(feed.feed_payloads_key()),
                _slot(feed.feed_sequence_key()),
                _slot(feed.event_key("event-1")),
            }
        )
        == 1
    )
    assert (
        len(
            {
                _slot(dlq.record_key("record-1")),
                _slot(dlq.records_key()),
                _slot(dlq.failed_tasks_index_key()),
                _slot(dlq.replay_lock_key("record-1")),
            }
        )
        == 1
    )
    assert (
        len(
            {
                _slot(leases._lease_key("lease-1")),
                _slot(leases._owner_key("worker-1")),
                _slot(leases._expiries_key),
                _slot(leases._expired_claims_key),
            }
        )
        == 1
    )


def test_workflow_keys_share_the_slots_required_by_each_operation() -> None:
    redis = object()
    contracts = RedisWorkflowContractStore(redis, prefix="workflow")  # type: ignore[arg-type]
    payload = {"request_id": "request-1"}
    dedup = contracts._dedup_key(
        stage="planner",
        task_id="task-1",
        action="plan",
        payload=payload,
        dedup_key_fields=("request_id",),
    )
    inflight = contracts._inflight_key(stage="planner", task_id="task-1")

    assert _slot(dedup) == _slot(inflight)
    assert _slot(run_state_key("workflow", "task-1")) == _slot(fanin_key("workflow", "task-1", "aggregate"))
