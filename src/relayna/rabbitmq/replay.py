from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .client import RelaynaRabbitClient


async def replay_raw_to_queue(
    client: RelaynaRabbitClient,
    *,
    queue_name: str,
    body: bytes,
    correlation_id: str | None = None,
    headers: Mapping[str, Any] | None = None,
    content_type: str | None = "application/json",
) -> None:
    await client.publish_raw_to_queue(
        queue_name,
        body,
        correlation_id=correlation_id,
        headers=headers,
        content_type=content_type,
    )


__all__ = ["replay_raw_to_queue"]
