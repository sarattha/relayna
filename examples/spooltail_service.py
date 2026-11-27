"""Run a single-task HTTP service and worker with the official Spooltail client."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI
from relayna_spooltail import SpooltailClient

from relayna.contracts import TaskEnvelope
from relayna.spooltail import SpooltailTaskConsumer, SpooltailTaskContext, create_spooltail_status_router

_SERVICE = os.environ.get("RELAYNA_SERVICE", "reference")
_TASK_TOPIC = os.environ.get("RELAYNA_TASK_TOPIC", "reference.tasks")
_STATUS_TOPIC = os.environ.get("RELAYNA_STATUS_TOPIC", "reference.status")
_WORKER_GROUP = os.environ.get("RELAYNA_WORKER_GROUP", "reference.workers")
_CELL_INPUT_LIMIT = 256 * 1024
_CELL_CLEANUP_SECONDS = 35
_CELL_STARTUP_SECONDS = 5


async def native_handler(task: TaskEnvelope, context: SpooltailTaskContext) -> None:
    await context.publish_status(status="processing")
    await context.publish_status(status="completed", result={"echo": task.payload})


def cell_handler(
    launcher: str, image: str, socket_path: str, timeout: int
) -> Callable[[TaskEnvelope, SpooltailTaskContext], Awaitable[None]]:
    if not Path(launcher).is_absolute() or not image or not Path(socket_path).is_absolute() or not 1 <= timeout <= 300:
        raise ValueError(
            "Cell requires absolute launcher and socket paths, a fixed image, and a timeout of 1–300 seconds"
        )
    arguments = (launcher, "--image", image, "--socket", socket_path, "--timeout", str(timeout))

    async def handle(task: TaskEnvelope, context: SpooltailTaskContext) -> None:
        await context.publish_status(status="processing")
        request = json.dumps(
            {"task_id": task.task_id, "attempt_id": context.attempt_id, "payload": task.payload},
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
        if len(request) > _CELL_INPUT_LIMIT:
            await context.publish_status(status="failed", message="Task exceeds the Cell input limit")
            return

        process = await asyncio.create_subprocess_exec(
            *arguments,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={},
        )
        exchange = asyncio.create_task(process.communicate(request))
        try:
            output, _ = await asyncio.wait_for(
                asyncio.shield(exchange), timeout=timeout + _CELL_STARTUP_SECONDS + _CELL_CLEANUP_SECONDS
            )
        except TimeoutError:
            with suppress(ProcessLookupError):
                process.kill()
            await exchange
            raise
        except asyncio.CancelledError:
            try:
                with suppress(ProcessLookupError):
                    process.terminate()
                await asyncio.wait_for(asyncio.shield(exchange), timeout=_CELL_CLEANUP_SECONDS)
            except TimeoutError:
                with suppress(ProcessLookupError):
                    process.kill()
                await exchange
            raise

        response = json.loads(output)
        if (
            not isinstance(response, dict)
            or response.get("task_id") != task.task_id
            or response.get("attempt_id") != context.attempt_id
        ):
            raise ValueError("Cell returned an invalid or uncorrelated response")

        cell_meta = {
            key: value for key in ("cell_id", "execution_id") if isinstance(value := response.get(key), str) and value
        }
        meta = {"cell": cell_meta} if cell_meta else {}
        result = response.get("result")
        if (
            process.returncode == 0
            and response.get("status") == "completed"
            and "cell_id" in cell_meta
            and isinstance(result, dict)
        ):
            await context.publish_status(status="completed", result=result, meta=meta)
        else:
            await context.publish_status(status="failed", message="Cell task did not complete", meta=meta)

    return handle


@asynccontextmanager
async def lifespan(app: FastAPI):
    launcher = os.environ.get("RELAYNA_CELL_TASK_LAUNCHER")
    handler = (
        cell_handler(
            launcher,
            os.environ["RELAYNA_CELL_IMAGE"],
            os.environ.get("RELAYNA_CELL_SOCKET", "/run/relayna-cell/api.sock"),
            int(os.environ.get("RELAYNA_CELL_TIMEOUT", "60")),
        )
        if launcher
        else native_handler
    )
    async with SpooltailClient(
        os.environ.get("SPOOLTAIL_TARGET", "127.0.0.1:7443"), tls=os.environ.get("SPOOLTAIL_TLS") == "on"
    ) as client:
        await client.ping()
        app.state.spooltail = client
        worker = SpooltailTaskConsumer(
            client=client, handler=handler, task_topic=_TASK_TOPIC, status_topic=_STATUS_TOPIC, group=_WORKER_GROUP
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
        get_client=lambda: app.state.spooltail, task_topic=_TASK_TOPIC, status_topic=_STATUS_TOPIC, service=_SERVICE
    )
)
