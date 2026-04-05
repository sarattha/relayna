def clamp_ttl_seconds(value: int | None, *, default: int = 86400, minimum: int = 60) -> int:
    if value is None:
        return default
    return max(minimum, int(value))


__all__ = ["clamp_ttl_seconds"]
