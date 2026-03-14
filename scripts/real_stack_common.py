from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
from fastapi import FastAPI

from relayna.config import RelaynaTopologyConfig
from relayna.fastapi import create_relayna_lifespan, create_status_router, get_relayna_runtime


def unique_suffix() -> str:
    return uuid4().hex[:8]


def build_config(suffix: str) -> RelaynaTopologyConfig:
    return RelaynaTopologyConfig(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange=f"relayna.tasks.exchange.{suffix}",
        tasks_queue=f"relayna.tasks.queue.{suffix}",
        tasks_routing_key=f"relayna.task.request.{suffix}",
        status_exchange=f"relayna.status.exchange.{suffix}",
        status_queue=f"relayna.status.queue.{suffix}",
    )


def build_app(config: RelaynaTopologyConfig, suffix: str) -> FastAPI:
    app = FastAPI(
        lifespan=create_relayna_lifespan(
            topology_config=config,
            redis_url="redis://localhost:6379/0",
            store_prefix=f"relayna-smoke-{suffix}",
        )
    )
    runtime = get_relayna_runtime(app)
    app.include_router(create_status_router(sse_stream=runtime.sse_stream, history_reader=runtime.history_reader))
    return app


@asynccontextmanager
async def app_client(app: FastAPI):
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://relayna.local") as client:
            yield client


async def poll_history(
    client: httpx.AsyncClient,
    *,
    task_id: str,
    expected_count: int,
    timeout_seconds: float = 10.0,
) -> dict[str, object]:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    last_response: dict[str, object] | None = None
    while asyncio.get_running_loop().time() < deadline:
        response = await client.get("/history", params={"task_id": task_id})
        response.raise_for_status()
        payload = response.json()
        last_response = payload
        if int(payload["count"]) >= expected_count:
            return payload
        await asyncio.sleep(0.1)
    raise TimeoutError(f"Timed out waiting for {expected_count} history events. Last response: {last_response}")


def parse_sse_events(text: str) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for block in text.strip().split("\n\n"):
        if not block.strip():
            continue
        record: dict[str, object] = {}
        for line in block.splitlines():
            if line.startswith(": "):
                record.setdefault("comments", []).append(line[2:])
            elif line.startswith("event: "):
                record["event"] = line[7:]
            elif line.startswith("data: "):
                record["data"] = json.loads(line[6:])
            elif line.startswith("id: "):
                record["id"] = line[4:]
        events.append(record)
    return events
