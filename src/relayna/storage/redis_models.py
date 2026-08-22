from .._redis import redis_key


def run_state_key(prefix: str, task_id: str) -> str:
    return redis_key(prefix, f"workflow:{task_id}", "workflow", "run", task_id)


def fanin_key(prefix: str, task_id: str, stage: str) -> str:
    return redis_key(prefix, f"workflow:{task_id}", "workflow", "fanin", task_id, stage)


__all__ = ["fanin_key", "run_state_key"]
