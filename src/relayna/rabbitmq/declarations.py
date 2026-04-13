from __future__ import annotations

from .client import RelaynaRabbitClient, declare_stream_queue


async def ensure_all(client: RelaynaRabbitClient) -> None:
    await client.initialize()


async def ensure_status_queue(client: RelaynaRabbitClient) -> str:
    return await client.ensure_status_queue()


async def ensure_tasks_queue(client: RelaynaRabbitClient) -> str:
    return await client.ensure_tasks_queue()


__all__ = ["declare_stream_queue", "ensure_all", "ensure_status_queue", "ensure_tasks_queue"]
