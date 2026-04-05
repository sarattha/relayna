def run_state_key(prefix: str, task_id: str) -> str:
    return f"{prefix}:workflow:run:{task_id}"


def fanin_key(prefix: str, task_id: str, stage: str) -> str:
    return f"{prefix}:workflow:fanin:{task_id}:{stage}"


__all__ = ["fanin_key", "run_state_key"]
