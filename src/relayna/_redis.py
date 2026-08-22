from __future__ import annotations

import hashlib

from redis.asyncio import Redis, RedisCluster

type RedisClient = Redis | RedisCluster


def redis_key(prefix: str, slot: str, *segments: str) -> str:
    """Build a readable Redis key with a stable cluster hash tag."""
    material = f"{prefix}\x1f{slot}".encode()
    tag = hashlib.sha256(material).hexdigest()[:16]
    suffix = ":".join(segments)
    return f"{{relayna:{tag}}}:{prefix}:{suffix}"


__all__ = ["RedisClient", "redis_key"]
