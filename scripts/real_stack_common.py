from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
from fastapi import FastAPI

from relayna.contracts import ContractAliasConfig, TerminalStatusSet
from relayna.dlq import DLQService
from relayna.fastapi import create_dlq_router, create_relayna_lifespan, create_status_router, get_relayna_runtime
from relayna.topology import SharedTasksSharedStatusShardedAggregationTopology, SharedTasksSharedStatusTopology


def unique_suffix() -> str:
    return uuid4().hex[:8]


def build_shared_topology(suffix: str) -> SharedTasksSharedStatusTopology:
    return SharedTasksSharedStatusTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange=f"relayna.tasks.exchange.{suffix}",
        tasks_queue=f"relayna.tasks.queue.{suffix}",
        tasks_routing_key=f"relayna.task.request.{suffix}",
        status_exchange=f"relayna.status.exchange.{suffix}",
        status_queue=f"relayna.status.queue.{suffix}",
    )


def build_sharded_topology(suffix: str, *, shard_count: int = 4) -> SharedTasksSharedStatusShardedAggregationTopology:
    return SharedTasksSharedStatusShardedAggregationTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange=f"relayna.tasks.exchange.{suffix}",
        tasks_queue=f"relayna.tasks.queue.{suffix}",
        tasks_routing_key=f"relayna.task.request.{suffix}",
        status_exchange=f"relayna.status.exchange.{suffix}",
        status_queue=f"relayna.status.queue.{suffix}",
        shard_count=shard_count,
        aggregation_queue_template=f"aggregation.queue.{suffix}.{{shard}}",
        aggregation_queue_name_prefix=f"aggregation.queue.{suffix}.shards",
    )


def build_app(
    topology: SharedTasksSharedStatusTopology,
    suffix: str,
    *,
    sse_terminal_statuses: TerminalStatusSet | None = None,
    dlq_store_prefix: str | None = None,
    alias_config: ContractAliasConfig | None = None,
) -> FastAPI:
    app = FastAPI(
        lifespan=create_relayna_lifespan(
            topology=topology,
            redis_url="redis://localhost:6379/0",
            store_prefix=f"relayna-smoke-{suffix}",
            sse_terminal_statuses=sse_terminal_statuses,
            dlq_store_prefix=dlq_store_prefix,
            alias_config=alias_config,
        )
    )
    runtime = get_relayna_runtime(app)
    app.include_router(
        create_status_router(
            sse_stream=runtime.sse_stream,
            history_reader=runtime.history_reader,
            latest_status_store=runtime.store,
            alias_config=alias_config,
        )
    )
    if runtime.dlq_store is not None:
        app.include_router(
            create_dlq_router(
                dlq_service=DLQService(
                    rabbitmq=runtime.rabbitmq,
                    dlq_store=runtime.dlq_store,
                    status_store=runtime.store,
                ),
                alias_config=alias_config,
            )
        )
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
    task_param_name: str = "task_id",
) -> dict[str, object]:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    last_response: dict[str, object] | None = None
    while asyncio.get_running_loop().time() < deadline:
        response = await client.get("/history", params={task_param_name: task_id})
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


async def fetch_latest_status(client: httpx.AsyncClient, task_id: str) -> dict[str, object]:
    response = await client.get(f"/status/{task_id}")
    response.raise_for_status()
    return response.json()
