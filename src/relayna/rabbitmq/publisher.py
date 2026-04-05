from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .client import RelaynaRabbitClient


async def publish_task(client: RelaynaRabbitClient, task: Mapping[str, Any]) -> None:
    await client.publish_task(task)


async def publish_status(client: RelaynaRabbitClient, event: Mapping[str, Any]) -> None:
    await client.publish_status(event)


async def publish_workflow(client: RelaynaRabbitClient, payload: Mapping[str, Any]) -> None:
    await client.publish_workflow(payload)


async def publish_to_stage(client: RelaynaRabbitClient, payload: Mapping[str, Any], *, stage: str) -> None:
    await client.publish_to_stage(payload, stage=stage)


async def publish_to_entry(client: RelaynaRabbitClient, payload: Mapping[str, Any], *, route: str) -> None:
    await client.publish_to_entry(payload, route=route)


__all__ = ["publish_status", "publish_task", "publish_to_entry", "publish_to_stage", "publish_workflow"]
