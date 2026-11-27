"""Run a single-task HTTP service and worker with the official Spooltail client."""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from relayna_spooltail import SpooltailClient

from relayna.contracts import TaskEnvelope
from relayna.spooltail import (
    SpooltailCellProfile,
    SpooltailLocalProfile,
    SpooltailTaskConsumer,
    SpooltailTaskContext,
    create_spooltail_status_router,
)

_SERVICE = os.environ.get("RELAYNA_SERVICE", "reference")
_TASK_TOPIC = os.environ.get("RELAYNA_TASK_TOPIC", "reference.tasks")
_STATUS_TOPIC = os.environ.get("RELAYNA_STATUS_TOPIC", "reference.status")
_WORKER_GROUP = os.environ.get("RELAYNA_WORKER_GROUP", "reference.workers")
_CANCEL_TOPIC = os.environ.get("RELAYNA_CANCEL_TOPIC") or None


async def native_handler(task: TaskEnvelope, context: SpooltailTaskContext) -> None:
    await context.publish_status(status="processing")
    await context.publish_status(status="completed", result={"echo": task.payload})


@asynccontextmanager
async def lifespan(app: FastAPI):
    launcher = os.environ.get("RELAYNA_CELL_TASK_LAUNCHER")
    profile = (
        SpooltailCellProfile(
            launcher,
            os.environ["RELAYNA_CELL_IMAGE"],
            os.environ.get("RELAYNA_CELL_SOCKET", "/run/relayna-cell/api.sock"),
            int(os.environ.get("RELAYNA_CELL_TIMEOUT", "60")),
            inference=os.environ.get("RELAYNA_CELL_INFERENCE") in {"1", "on"},
        )
        if launcher
        else SpooltailLocalProfile(native_handler)
    )
    async with SpooltailClient(
        os.environ.get("SPOOLTAIL_TARGET", "127.0.0.1:7443"), tls=os.environ.get("SPOOLTAIL_TLS") == "on"
    ) as client:
        await client.ping()
        app.state.spooltail = client
        worker = SpooltailTaskConsumer(
            client=client,
            profile=profile,
            task_topic=_TASK_TOPIC,
            status_topic=_STATUS_TOPIC,
            group=_WORKER_GROUP,
            cancel_topic=_CANCEL_TOPIC,
        )
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(worker.run_forever())
            try:
                yield
            finally:
                worker.stop()


app = FastAPI(lifespan=lifespan)
app.include_router(
    create_spooltail_status_router(
        get_client=lambda: app.state.spooltail,
        task_topic=_TASK_TOPIC,
        status_topic=_STATUS_TOPIC,
        service=_SERVICE,
        cancel_topic=_CANCEL_TOPIC,
        broker_version=os.environ.get("SPOOLTAIL_BROKER_VERSION"),
    )
)
