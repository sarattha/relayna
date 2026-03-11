from relayna.rabbitmq import ShardRoutingStrategy, TaskIdRoutingStrategy


def test_task_id_routing_uses_task_id() -> None:
    strategy = TaskIdRoutingStrategy("task.request")
    assert strategy.task_routing_key({"task_id": "abc"}) == "task.request"
    assert strategy.status_routing_key({"task_id": "abc"}) == "abc"


def test_shard_routing_uses_parent_task_id_when_present() -> None:
    strategy = ShardRoutingStrategy("task.request", shard_count=2)
    key = strategy.status_routing_key({"task_id": "child", "meta": {"parent_task_id": "parent"}})
    assert key in {"agg.0", "agg.1"}
